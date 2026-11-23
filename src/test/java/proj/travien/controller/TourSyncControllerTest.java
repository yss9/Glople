package proj.travien.controller;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import proj.travien.service.TourSyncRunner;

import java.lang.reflect.Method;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

// 스프링 컨텍스트 없이 컨트롤러와 매핑 애너테이션만 확인한다.
class TourSyncControllerTest {

    private final TourSyncRunner runner = mock(TourSyncRunner.class);
    private final TourSyncController controller = new TourSyncController(runner);

    @Test
    void startsSummaryRebuildInBackground() {
        when(runner.startSummaryRebuild()).thenReturn(TourSyncRunner.StartResult.STARTED);

        assertThat(controller.rebuildSummaries().getStatusCode()).isEqualTo(HttpStatus.ACCEPTED);
        verify(runner).startSummaryRebuild();
    }

    @Test
    void reportsConflictWhileAnotherSyncRuns() {
        when(runner.startSummaryRebuild()).thenReturn(TourSyncRunner.StartResult.ALREADY_RUNNING);

        assertThat(controller.rebuildSummaries().getStatusCode()).isEqualTo(HttpStatus.CONFLICT);
    }

    @Test
    void mapsSummaryRebuildToAdminOnlyPost() throws NoSuchMethodException {
        Method method = TourSyncController.class.getMethod("rebuildSummaries");

        assertThat(TourSyncController.class.getAnnotation(RequestMapping.class).value()).containsExactly("/api/admin/tour-sync");
        assertThat(method.getAnnotation(PostMapping.class).value()).containsExactly("/summaries");
        assertThat(method.getAnnotation(PreAuthorize.class).value()).isEqualTo("hasRole('ADMIN')");
    }
}
