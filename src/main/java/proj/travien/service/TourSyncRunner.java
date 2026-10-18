package proj.travien.service;

import jakarta.annotation.PreDestroy;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import proj.travien.dto.TourSyncResult;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Supplier;

/**
 * 여행지 동기화(국내 TourAPI, 해외 위키데이터·위키백과)를 요청 스레드 밖에서 한 번에 하나씩 실행하고, 마지막 실행 상태를 들고 있는다.
 * 모두 destination_info를 고치므로 종류와 상관없이 동시에 하나만 돈다.
 */
@Slf4j
@Component
public class TourSyncRunner {

    public enum Task { DESTINATIONS, OVERVIEWS, SUMMARIES, WIKI_DESTINATIONS, WIKI_DESCRIPTIONS, WIKI_SUMMARIES }

    public enum StartResult { STARTED, ALREADY_RUNNING, NOT_CONFIGURED }

    public record Status(
            boolean running,
            Task task,
            LocalDateTime startedAt,
            LocalDateTime finishedAt,
            TourSyncResult lastResult,
            String lastError
    ) {
        private static final Status IDLE = new Status(false, null, null, null, null, null);
    }

    private final TourDestinationSyncService tourDestinationSyncService;
    private final WikiDestinationSyncService wikiDestinationSyncService;
    private final TourApiClient tourApiClient;
    private final int scheduledOverviewLimit;
    private final ExecutorService executor;
    private final AtomicBoolean running = new AtomicBoolean(false);
    private volatile Status status = Status.IDLE;

    @Autowired
    public TourSyncRunner(
            TourDestinationSyncService tourDestinationSyncService,
            WikiDestinationSyncService wikiDestinationSyncService,
            TourApiClient tourApiClient,
            @Value("${tour.sync.overview-daily-limit:900}") int scheduledOverviewLimit
    ) {
        this(tourDestinationSyncService, wikiDestinationSyncService, tourApiClient, scheduledOverviewLimit,
                Executors.newSingleThreadExecutor(runnable -> {
                    Thread thread = new Thread(runnable, "tour-sync");
                    thread.setDaemon(true);
                    return thread;
                }));
    }

    // 테스트에서 실행 시점을 직접 정할 수 있도록 Executor를 받는 생성자
    TourSyncRunner(
            TourDestinationSyncService tourDestinationSyncService,
            WikiDestinationSyncService wikiDestinationSyncService,
            TourApiClient tourApiClient,
            int scheduledOverviewLimit,
            ExecutorService executor
    ) {
        this.tourDestinationSyncService = tourDestinationSyncService;
        this.wikiDestinationSyncService = wikiDestinationSyncService;
        this.tourApiClient = tourApiClient;
        this.scheduledOverviewLimit = scheduledOverviewLimit;
        this.executor = executor;
    }

    public StartResult startDestinationSync() {
        return start(Task.DESTINATIONS, true, tourDestinationSyncService::syncDestinations);
    }

    public StartResult startOverviewSync(int limit) {
        return start(Task.OVERVIEWS, true, () -> tourDestinationSyncService.syncOverviews(limit));
    }

    // 소개·마스크 재계산은 DB에 있는 값만 쓰므로 인증키가 없어도 실행한다. 같은 행을 고치는 다른 동기화와는 동시에 돌리지 않는다.
    public StartResult startSummaryRebuild() {
        return start(Task.SUMMARIES, false, tourDestinationSyncService::rebuildSummaries);
    }

    // 위키데이터·위키백과는 인증키가 없으므로 TourAPI 키 없이도 실행한다. (NOT_CONFIGURED는 TourAPI 작업에만 해당)
    public StartResult startWikiDestinationSync(List<String> countryCodes, int limitPerCountry) {
        List<String> codes = countryCodes == null ? List.of() : List.copyOf(countryCodes);
        return start(Task.WIKI_DESTINATIONS, false, () -> wikiDestinationSyncService.syncDestinations(codes, limitPerCountry));
    }

    public StartResult startWikiDescriptionSync(int limit) {
        return start(Task.WIKI_DESCRIPTIONS, false, () -> wikiDestinationSyncService.syncDescriptions(limit));
    }

    public StartResult startWikiSummaryRebuild() {
        return start(Task.WIKI_SUMMARIES, false, wikiDestinationSyncService::rebuildSummaries);
    }

    public Status status() {
        return status;
    }

    /**
     * 동기화와 같은 행을 고치는 짧은 작업(태거 예측 가져오기)을 부른 스레드에서 바로 실행한다. 다른 동기화가 돌고 있으면 실행하지 않고 빈 값.
     * 실행하는 동안에는 동기화를 시작할 수 없다(ALREADY_RUNNING). 동기화 상태(status)는 바꾸지 않는다.
     * 동기화는 행을 읽어 두었다가 통째로 다시 저장하므로, 동시에 돌면 한쪽이 쓴 열을 다른 쪽이 예전 값으로 덮어쓸 수 있어서 막는다.
     */
    public <T> Optional<T> runExclusively(Supplier<T> job) {
        if (!running.compareAndSet(false, true)) {
            return Optional.empty();
        }
        try {
            return Optional.of(Objects.requireNonNull(job.get(), "작업 결과가 null입니다."));
        } finally {
            running.set(false);
        }
    }

    // tour.sync.overview-cron을 설정하면 매일 일일 한도 안에서 설명문을 이어 받는다. 기본값 "-"는 꺼짐.
    @Scheduled(cron = "${tour.sync.overview-cron:-}")
    public void runScheduledOverviewSync() {
        StartResult result = startOverviewSync(scheduledOverviewLimit);
        log.info("Scheduled tour overview sync limit={} result={}", scheduledOverviewLimit, result);
    }

    private StartResult start(Task task, boolean callsTourApi, Supplier<TourSyncResult> job) {
        if (callsTourApi && !tourApiClient.isConfigured()) {
            return StartResult.NOT_CONFIGURED;
        }
        if (!running.compareAndSet(false, true)) {
            return StartResult.ALREADY_RUNNING;
        }

        LocalDateTime startedAt = LocalDateTime.now();
        status = new Status(true, task, startedAt, null, null, null);
        executor.execute(() -> {
            try {
                TourSyncResult result = job.get();
                status = new Status(false, task, startedAt, LocalDateTime.now(), result, null);
                log.info("Tour sync finished task={} result={}", task, result);
            } catch (Throwable e) {
                // OutOfMemoryError 같은 Error도 상태에 남겨야 /status가 계속 진행 중으로 보이지 않는다.
                String message = e.getMessage() != null ? e.getMessage() : e.getClass().getSimpleName();
                status = new Status(false, task, startedAt, LocalDateTime.now(), null, message);
                log.error("Tour sync failed task={}", task, e);
                if (e instanceof Error error) {
                    throw error;
                }
            } finally {
                running.set(false);
            }
        });
        return StartResult.STARTED;
    }

    @PreDestroy
    public void shutdown() {
        executor.shutdownNow();
    }
}
