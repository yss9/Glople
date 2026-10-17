package proj.travien.controller;

import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import proj.travien.service.TourSyncRunner;

import java.lang.reflect.Method;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

// 스프링 컨텍스트 없이 컨트롤러와 매핑 애너테이션만 확인한다.
class WikiSyncControllerTest {

    private final TourSyncRunner runner = mock(TourSyncRunner.class);
    private final WikiSyncController controller = new WikiSyncController(runner);

    @Test
    void startsDestinationSyncWithNormalizedCountryCodes() {
        when(runner.startWikiDestinationSync(List.of("FR", "JP"), 150)).thenReturn(TourSyncRunner.StartResult.STARTED);

        assertThat(controller.syncDestinations(List.of(" fr", "JP", "fr", ""), 150).getStatusCode()).isEqualTo(HttpStatus.ACCEPTED);
        verify(runner).startWikiDestinationSync(List.of("FR", "JP"), 150);
    }

    @Test
    void startsAllCountriesWhenNoneGiven() {
        when(runner.startWikiDestinationSync(List.of(), 150)).thenReturn(TourSyncRunner.StartResult.STARTED);

        assertThat(controller.syncDestinations(null, 150).getStatusCode()).isEqualTo(HttpStatus.ACCEPTED);
        verify(runner).startWikiDestinationSync(List.of(), 150);
    }

    @Test
    void rejectsBadParameters() {
        assertThat(controller.syncDestinations(List.of("FRA"), 150).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(controller.syncDestinations(List.of("KR"), 150).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(controller.syncDestinations(List.of("FR"), 0).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(controller.syncDestinations(List.of("FR"), 501).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(controller.syncDescriptions(0).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        assertThat(controller.syncDescriptions(20_001).getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST);
        verify(runner, never()).startWikiDestinationSync(anyList(), anyInt());
        verify(runner, never()).startWikiDescriptionSync(anyInt());
    }

    @Test
    void reportsConflictWhileAnotherSyncRuns() {
        when(runner.startWikiDescriptionSync(1000)).thenReturn(TourSyncRunner.StartResult.ALREADY_RUNNING);
        when(runner.startWikiSummaryRebuild()).thenReturn(TourSyncRunner.StartResult.STARTED);

        assertThat(controller.syncDescriptions(1000).getStatusCode()).isEqualTo(HttpStatus.CONFLICT);
        assertThat(controller.rebuildSummaries().getStatusCode()).isEqualTo(HttpStatus.ACCEPTED);
    }

    @Test
    void mapsEndpointsToAdminOnlyRoutes() throws NoSuchMethodException {
        assertThat(WikiSyncController.class.getAnnotation(RequestMapping.class).value()).containsExactly("/api/admin/wiki-sync");

        Method destinations = WikiSyncController.class.getMethod("syncDestinations", List.class, int.class);
        Method descriptions = WikiSyncController.class.getMethod("syncDescriptions", int.class);
        Method summaries = WikiSyncController.class.getMethod("rebuildSummaries");
        Method status = WikiSyncController.class.getMethod("getStatus");
        assertThat(destinations.getAnnotation(PostMapping.class).value()).containsExactly("/destinations");
        assertThat(descriptions.getAnnotation(PostMapping.class).value()).containsExactly("/descriptions");
        assertThat(summaries.getAnnotation(PostMapping.class).value()).containsExactly("/summaries");
        assertThat(status.getAnnotation(GetMapping.class).value()).containsExactly("/status");
        for (Method method : List.of(destinations, descriptions, summaries, status)) {
            assertThat(method.getAnnotation(PreAuthorize.class).value()).isEqualTo("hasRole('ADMIN')");
        }
    }
}
