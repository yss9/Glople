package proj.travien.service;

import org.junit.jupiter.api.Test;
import proj.travien.dto.TourSyncResult;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.List;
import java.util.concurrent.AbstractExecutorService;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class TourSyncRunnerTest {

    private final TourDestinationSyncService syncService = mock(TourDestinationSyncService.class);
    private final WikiDestinationSyncService wikiSyncService = mock(WikiDestinationSyncService.class);
    private final TourApiClient client = mock(TourApiClient.class);
    private final ManualExecutor executor = new ManualExecutor();
    private final TourSyncRunner runner = new TourSyncRunner(syncService, wikiSyncService, client, 900, executor);

    @Test
    void refusesToStartWithoutServiceKey() {
        when(client.isConfigured()).thenReturn(false);

        assertThat(runner.startDestinationSync()).isEqualTo(TourSyncRunner.StartResult.NOT_CONFIGURED);
        assertThat(executor.pending()).isZero();
        assertThat(runner.status().running()).isFalse();
        verifyNoInteractions(syncService);
    }

    @Test
    void rebuildsSummariesWithoutServiceKey() {
        // 소개 재계산은 TourAPI를 부르지 않으므로 인증키가 없어도 실행한다.
        when(client.isConfigured()).thenReturn(false);
        TourSyncResult result = new TourSyncResult(19_949, 0, 19_949, 0, false);
        when(syncService.rebuildSummaries()).thenReturn(result);

        assertThat(runner.startSummaryRebuild()).isEqualTo(TourSyncRunner.StartResult.STARTED);
        assertThat(runner.status().task()).isEqualTo(TourSyncRunner.Task.SUMMARIES);
        assertThat(runner.startDestinationSync()).isEqualTo(TourSyncRunner.StartResult.NOT_CONFIGURED);

        executor.runPending();

        assertThat(runner.status().running()).isFalse();
        assertThat(runner.status().lastResult()).isEqualTo(result);
        verify(syncService).rebuildSummaries();
        verify(syncService, never()).syncDestinations();
    }

    @Test
    void runsWikiTasksWithoutTourApiKey() {
        // 위키데이터·위키백과는 인증키가 없으므로 TourAPI 키가 없어도 실행한다.
        when(client.isConfigured()).thenReturn(false);
        TourSyncResult destinations = new TourSyncResult(300, 300, 0, 0, false);
        TourSyncResult descriptions = new TourSyncResult(40, 0, 38, 2, false);
        TourSyncResult summaries = new TourSyncResult(300, 0, 5, 0, false);
        when(wikiSyncService.syncDestinations(List.of("FR", "JP"), 150)).thenReturn(destinations);
        when(wikiSyncService.syncDescriptions(40)).thenReturn(descriptions);
        when(wikiSyncService.rebuildSummaries()).thenReturn(summaries);

        assertThat(runner.startWikiDestinationSync(List.of("FR", "JP"), 150)).isEqualTo(TourSyncRunner.StartResult.STARTED);
        assertThat(runner.status().task()).isEqualTo(TourSyncRunner.Task.WIKI_DESTINATIONS);
        executor.runPending();
        assertThat(runner.status().lastResult()).isEqualTo(destinations);

        assertThat(runner.startWikiDescriptionSync(40)).isEqualTo(TourSyncRunner.StartResult.STARTED);
        assertThat(runner.status().task()).isEqualTo(TourSyncRunner.Task.WIKI_DESCRIPTIONS);
        executor.runPending();
        assertThat(runner.status().lastResult()).isEqualTo(descriptions);

        assertThat(runner.startWikiSummaryRebuild()).isEqualTo(TourSyncRunner.StartResult.STARTED);
        executor.runPending();
        assertThat(runner.status().task()).isEqualTo(TourSyncRunner.Task.WIKI_SUMMARIES);
        assertThat(runner.status().lastResult()).isEqualTo(summaries);

        // TourAPI 작업은 여전히 키가 있어야 한다.
        assertThat(runner.startDestinationSync()).isEqualTo(TourSyncRunner.StartResult.NOT_CONFIGURED);
        verifyNoInteractions(syncService);
    }

    @Test
    void doesNotRunWikiAndTourSyncAtTheSameTime() {
        // 둘 다 destination_info를 저장하므로 하나만 돈다.
        when(client.isConfigured()).thenReturn(true);
        when(syncService.syncOverviews(10)).thenReturn(new TourSyncResult(1, 0, 1, 0, false));
        when(wikiSyncService.syncDescriptions(20)).thenReturn(new TourSyncResult(20, 0, 20, 0, false));

        runner.startOverviewSync(10);
        assertThat(runner.startWikiDestinationSync(List.of(), 150)).isEqualTo(TourSyncRunner.StartResult.ALREADY_RUNNING);
        assertThat(runner.startWikiDescriptionSync(20)).isEqualTo(TourSyncRunner.StartResult.ALREADY_RUNNING);
        executor.runPending();

        assertThat(runner.startWikiDescriptionSync(20)).isEqualTo(TourSyncRunner.StartResult.STARTED);
        assertThat(runner.startDestinationSync()).isEqualTo(TourSyncRunner.StartResult.ALREADY_RUNNING);
        executor.runPending();
        verify(wikiSyncService).syncDescriptions(20);
        verify(wikiSyncService, never()).syncDestinations(any(), anyInt());
    }

    @Test
    void recordsWikiFailureLikeTourFailure() {
        when(wikiSyncService.syncDestinations(List.of(), 150))
                .thenThrow(new IllegalArgumentException("수집 대상 국가 목록에 없는 국가 코드입니다: XX"));

        runner.startWikiDestinationSync(null, 150);
        executor.runPending();

        assertThat(runner.status().running()).isFalse();
        assertThat(runner.status().lastError()).contains("XX");
    }

    @Test
    void doesNotRebuildSummariesWhileAnotherSyncRuns() {
        when(client.isConfigured()).thenReturn(true);
        when(syncService.syncOverviews(10)).thenReturn(new TourSyncResult(1, 0, 1, 0, false));

        runner.startOverviewSync(10);

        // 설명문 수집과 같은 행을 저장하므로 동시에 돌리지 않는다.
        assertThat(runner.startSummaryRebuild()).isEqualTo(TourSyncRunner.StartResult.ALREADY_RUNNING);
        executor.runPending();
        assertThat(runner.startSummaryRebuild()).isEqualTo(TourSyncRunner.StartResult.STARTED);
    }

    @Test
    void runsOneSyncAtATimeAndKeepsLastResult() {
        when(client.isConfigured()).thenReturn(true);
        TourSyncResult result = new TourSyncResult(3, 3, 0, 0, false);
        when(syncService.syncDestinations()).thenReturn(result);

        assertThat(runner.startDestinationSync()).isEqualTo(TourSyncRunner.StartResult.STARTED);
        assertThat(runner.status().running()).isTrue();
        assertThat(runner.status().task()).isEqualTo(TourSyncRunner.Task.DESTINATIONS);
        assertThat(runner.startOverviewSync(10)).isEqualTo(TourSyncRunner.StartResult.ALREADY_RUNNING);

        executor.runPending();

        TourSyncRunner.Status status = runner.status();
        assertThat(status.running()).isFalse();
        assertThat(status.finishedAt()).isNotNull();
        assertThat(status.lastResult()).isEqualTo(result);
        assertThat(status.lastError()).isNull();
        assertThat(runner.startOverviewSync(10)).isEqualTo(TourSyncRunner.StartResult.STARTED);
    }

    @Test
    void recordsFailureAndAllowsNextRun() {
        when(client.isConfigured()).thenReturn(true);
        when(syncService.syncOverviews(10)).thenThrow(new IllegalStateException("설명문 수집이 연속 5회 실패해 중단했습니다"));

        runner.startOverviewSync(10);
        executor.runPending();

        TourSyncRunner.Status status = runner.status();
        assertThat(status.running()).isFalse();
        assertThat(status.task()).isEqualTo(TourSyncRunner.Task.OVERVIEWS);
        assertThat(status.finishedAt()).isNotNull();
        assertThat(status.lastError()).contains("연속 5회");
        assertThat(runner.startDestinationSync()).isEqualTo(TourSyncRunner.StartResult.STARTED);
    }

    @Test
    void doesNotReportRunningAfterError() {
        when(client.isConfigured()).thenReturn(true);
        when(syncService.syncDestinations()).thenThrow(new OutOfMemoryError("Java heap space"));

        runner.startDestinationSync();

        // Error는 상태를 남긴 뒤 다시 던진다.
        assertThatThrownBy(executor::runPending).isInstanceOf(OutOfMemoryError.class);
        TourSyncRunner.Status status = runner.status();
        assertThat(status.running()).isFalse();
        assertThat(status.finishedAt()).isNotNull();
        assertThat(status.lastError()).isEqualTo("Java heap space");
        assertThat(runner.startDestinationSync()).isEqualTo(TourSyncRunner.StartResult.STARTED);
    }

    // 넘겨받은 작업을 테스트가 runPending()을 부를 때까지 실행하지 않는 Executor
    private static class ManualExecutor extends AbstractExecutorService {

        private final Deque<Runnable> tasks = new ArrayDeque<>();

        int pending() {
            return tasks.size();
        }

        void runPending() {
            while (!tasks.isEmpty()) {
                tasks.poll().run();
            }
        }

        @Override
        public void execute(Runnable command) {
            tasks.add(command);
        }

        @Override
        public void shutdown() {
        }

        @Override
        public List<Runnable> shutdownNow() {
            return List.copyOf(tasks);
        }

        @Override
        public boolean isShutdown() {
            return false;
        }

        @Override
        public boolean isTerminated() {
            return false;
        }

        @Override
        public boolean awaitTermination(long timeout, TimeUnit unit) {
            return true;
        }
    }
}
