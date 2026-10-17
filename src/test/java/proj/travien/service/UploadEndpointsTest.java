package proj.travien.service;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;
import proj.travien.controller.ChatController;
import proj.travien.controller.FileController;
import proj.travien.controller.PostController;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

// 스프링 컨텍스트 없이 업로드 엔드포인트가 임시 디렉터리 안에 서버 생성 이름으로만 저장하는지 확인한다.
class UploadEndpointsTest {

    private static final String UUID_NAME = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";

    @TempDir
    Path tempDir;

    @Test
    void postImageUploadKeepsImagePrefixAndStaysInsideUploadDirectory() throws IOException {
        Path imageDir = Files.createDirectories(tempDir.resolve("static/image"));
        PostController controller = new PostController();
        ReflectionTestUtils.setField(controller, "uploadDir", imageDir + "/");

        String url = controller.uploadImage(file("image", "../../../../tmp/evil.PNG", "png"));

        assertThat(url).matches("/image/" + UUID_NAME + "\\.png");
        assertThat(filesUnder(tempDir)).containsExactly(imageDir.resolve(url.substring("/image/".length())));
    }

    @Test
    void postImageUploadRejectsScriptsAndTraversal() throws IOException {
        Path imageDir = Files.createDirectories(tempDir.resolve("static/image"));
        PostController controller = new PostController();
        ReflectionTestUtils.setField(controller, "uploadDir", imageDir + "/");

        assertThat(controller.uploadImage(file("image", "..\\..\\shell.jsp", "<%= 1 %>"))).isEqualTo("허용되지 않는 파일입니다.");
        assertThat(controller.uploadImage(file("image", "/etc/passwd", "root"))).isEqualTo("허용되지 않는 파일입니다.");
        assertThat(filesUnder(tempDir)).isEmpty();
    }

    @Test
    void chatFileUploadReturnsImageUrlWithGeneratedName() throws IOException {
        Path imageDir = Files.createDirectories(tempDir.resolve("static/image"));
        ChatController controller = new ChatController();
        // application.properties 의 upload.location 처럼 끝에 / 가 붙은 값
        ReflectionTestUtils.setField(controller, "uploadDir", imageDir + "/");

        ResponseEntity<String> response = controller.uploadFile("a_b", file("file", "..%2F..%2Fticket.PDF", "pdf"), "a");

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(response.getBody()).matches("/image/" + UUID_NAME + "\\.pdf");
        assertThat(filesUnder(tempDir)).containsExactly(imageDir.resolve(response.getBody().substring("/image/".length())));
    }

    @Test
    void chatFileUploadRejectsActiveContent() throws IOException {
        Path imageDir = Files.createDirectories(tempDir.resolve("static/image"));
        ChatController controller = new ChatController();
        ReflectionTestUtils.setField(controller, "uploadDir", imageDir.toString());

        for (String name : List.of("page.html", "../../index.html", "image.svg", "noextension", "photo.png.js")) {
            ResponseEntity<String> response = controller.uploadFile("a_b", file("file", name, "<script>"), "a");
            assertThat(response.getStatusCode()).as(name).isEqualTo(HttpStatus.BAD_REQUEST);
        }
        assertThat(filesUnder(tempDir)).isEmpty();
    }

    @Test
    void editorImageUploadUsesGeneratedNameAndRejectsNonImages() throws IOException {
        Path imageDir = Files.createDirectories(tempDir.resolve("static/image"));
        FileUploadeService service = new FileUploadeService();
        ReflectionTestUtils.setField(service, "UPLOAD_LOCATION", imageDir + "/");

        String name = service.saveFile(file("image", "../../evil.gif", "gif"));

        // 에디터는 "/image/" + 이름 으로 이미지를 넣는다
        assertThat(name).matches(UUID_NAME + "\\.gif");
        assertThat(Files.readString(imageDir.resolve(name))).isEqualTo("gif");
        assertThatThrownBy(() -> service.saveFile(file("image", "evil.html", "<script>")))
                .isInstanceOf(UploadPaths.InvalidUploadException.class);
        assertThat(filesUnder(tempDir)).containsExactly(imageDir.resolve(name));
    }

    @Test
    void uncaughtRejectionIsAnsweredWithBadRequest() throws Exception {
        // FileController 는 예외를 잡지 않으므로 InvalidUploadException 의 @ResponseStatus 로 400 이 나가야 한다
        Path imageDir = Files.createDirectories(tempDir.resolve("static/image"));
        FileUploadeService service = new FileUploadeService();
        ReflectionTestUtils.setField(service, "UPLOAD_LOCATION", imageDir + "/");
        FileController controller = new FileController();
        ReflectionTestUtils.setField(controller, "fileUploadeService", service);
        MockMvc mockMvc = MockMvcBuilders.standaloneSetup(controller).build();

        mockMvc.perform(multipart("/api/fileUpload/").file(file("image", "../../evil.html", "<script>")))
                .andExpect(status().isBadRequest());
        mockMvc.perform(multipart("/api/fileUpload/").file(file("image", "photo.jpg", "jpg")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.imageUrl").value(org.hamcrest.Matchers.matchesPattern(UUID_NAME + "\\.jpg")));
        assertThat(filesUnder(tempDir)).hasSize(1);
    }

    private static MockMultipartFile file(String param, String originalName, String content) {
        return new MockMultipartFile(param, originalName, "application/octet-stream", content.getBytes(StandardCharsets.UTF_8));
    }

    private static List<Path> filesUnder(Path dir) throws IOException {
        try (Stream<Path> paths = Files.walk(dir)) {
            return paths.filter(Files::isRegularFile).toList();
        }
    }
}
