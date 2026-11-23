package proj.travien.service;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.mock.web.MockMultipartFile;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class UploadPathsTest {

    private static final String UUID_NAME = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";

    @TempDir
    Path tempDir;

    @Test
    void keepsOnlyTheLastWhitelistedExtensionInLowerCase() {
        assertThat(UploadPaths.extensionOf("photo.jpg", UploadPaths.IMAGE_EXTENSIONS)).isEqualTo("jpg");
        assertThat(UploadPaths.extensionOf("여행 사진.PNG", UploadPaths.IMAGE_EXTENSIONS)).isEqualTo("png");
        assertThat(UploadPaths.extensionOf("  photo.jpeg  ", UploadPaths.IMAGE_EXTENSIONS)).isEqualTo("jpeg");
        assertThat(UploadPaths.extensionOf("ticket.PDF", UploadPaths.DOCUMENT_EXTENSIONS)).isEqualTo("pdf");
        // 이중 확장자는 마지막 확장자만 본다
        assertThat(UploadPaths.extensionOf("shell.php.png", UploadPaths.IMAGE_EXTENSIONS)).isEqualTo("png");
        assertThat(UploadPaths.extensionOf("archive.tar.gz.jpg", UploadPaths.IMAGE_EXTENSIONS)).isEqualTo("jpg");
    }

    @Test
    void ignoresDirectoryPartsOfTheClientName() {
        // 경로 조작이 섞여 있어도 확장자만 쓰고 이름은 버린다
        assertThat(UploadPaths.extensionOf("../../../etc/evil.png", UploadPaths.IMAGE_EXTENSIONS)).isEqualTo("png");
        assertThat(UploadPaths.extensionOf("..\\..\\windows\\evil.png", UploadPaths.IMAGE_EXTENSIONS)).isEqualTo("png");
        assertThat(UploadPaths.extensionOf("/var/www/evil.gif", UploadPaths.IMAGE_EXTENSIONS)).isEqualTo("gif");
        assertThat(UploadPaths.extensionOf("C:\\Users\\me\\evil.webp", UploadPaths.IMAGE_EXTENSIONS)).isEqualTo("webp");
        assertThat(UploadPaths.extensionOf("..%2F..%2Fevil.png", UploadPaths.IMAGE_EXTENSIONS)).isEqualTo("png");
        assertThat(UploadPaths.extensionOf("a".repeat(10_000) + ".png", UploadPaths.IMAGE_EXTENSIONS)).isEqualTo("png");
    }

    @ParameterizedTest
    @NullAndEmptySource
    @ValueSource(strings = {
            "   ",
            "README",                 // 확장자 없음
            "photo.",                 // 점으로 끝남
            ".png",                   // 점으로 시작하는 숨김 파일
            "../../etc/passwd",       // 확장자 없는 경로 조작
            "..\\..\\boot.ini",       // 허용되지 않는 확장자
            "/etc/passwd",
            "C:\\Windows\\win.ini",
            "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
            "..%2F..%2Fevil%2Epng",   // 인코딩된 점은 확장자로 보지 않는다
            "photo.png.php",          // 이중 확장자, 마지막이 위험한 형식
            "photo.png\u0000.jsp",    // 널 바이트
            "photo.png%00.jsp",
            "photo.svg",              // 스크립트 실행 가능한 이미지
            "photo.html",
            "photo.p-ng",
            "photo.pngpngpngpng"      // 확장자가 너무 김
    })
    void rejectsNamesWithoutAnAllowedExtension(String originalName) {
        assertThatThrownBy(() -> UploadPaths.extensionOf(originalName, UploadPaths.IMAGE_EXTENSIONS))
                .isInstanceOf(UploadPaths.InvalidUploadException.class)
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void documentWhitelistStillRejectsActiveContent() {
        for (String name : List.of("page.html", "page.htm", "image.svg", "script.js", "Shell.JSP", "run.exe", "data.xml")) {
            assertThatThrownBy(() -> UploadPaths.extensionOf(name, UploadPaths.DOCUMENT_EXTENSIONS))
                    .as(name)
                    .isInstanceOf(UploadPaths.InvalidUploadException.class);
        }
        assertThat(UploadPaths.DOCUMENT_EXTENSIONS).containsAll(UploadPaths.IMAGE_EXTENSIONS);
    }

    @Test
    void createsUuidNamesThatNeverContainTheClientName() {
        String name = UploadPaths.newFileName("../../evil name.JPG", UploadPaths.IMAGE_EXTENSIONS);

        assertThat(name).matches(UUID_NAME + "\\.jpg");
        Set<String> names = new HashSet<>();
        Stream.generate(() -> UploadPaths.newFileName("same.png", UploadPaths.IMAGE_EXTENSIONS)).limit(100).forEach(names::add);
        assertThat(names).hasSize(100);
    }

    @Test
    void resolvesGeneratedNamesDirectlyInsideTheBaseDirectory() {
        Path base = tempDir.resolve("image");

        Path target = UploadPaths.resolveInside(base, "1f0e2d3c-4b5a-6978-8796-a5b4c3d2e1f0.png");

        assertThat(target).isAbsolute();
        assertThat(target.getParent()).isEqualTo(base.toAbsolutePath().normalize());
        // 상대 경로나 정규화가 필요한 기준 경로도 같은 결과
        assertThat(UploadPaths.resolveInside(base.resolve("../image"), "a.png").getParent())
                .isEqualTo(base.toAbsolutePath().normalize());
    }

    @ParameterizedTest
    @NullAndEmptySource
    @ValueSource(strings = {
            "..", ".", "../evil.png", "..\\evil.png", "../../etc/passwd", "/etc/passwd", "C:\\evil.png",
            "sub/evil.png", "sub\\evil.png", "%2e%2e%2fevil.png", ".hidden.png", "evil .png", "evil\u0000.png"
    })
    void refusesNamesThatEscapeOrNestUnderTheBaseDirectory(String fileName) {
        assertThatThrownBy(() -> UploadPaths.resolveInside(tempDir, fileName))
                .isInstanceOf(UploadPaths.InvalidUploadException.class);
    }

    @Test
    void refusesVeryLongStoredNames() {
        assertThatThrownBy(() -> UploadPaths.resolveInside(tempDir, "a".repeat(300) + ".png"))
                .isInstanceOf(UploadPaths.InvalidUploadException.class);
    }

    @Test
    void checksWhetherAStoredPathStaysInsideADirectory() {
        Path base = tempDir.resolve("static/verification-files");

        assertThat(UploadPaths.isInside(base, base.resolve("a.pdf"))).isTrue();
        assertThat(UploadPaths.isInside(base, base.resolve("sub/../a.pdf"))).isTrue();
        assertThat(UploadPaths.isInside(base, base)).isFalse();
        assertThat(UploadPaths.isInside(base, base.resolve("../../application.properties"))).isFalse();
        assertThat(UploadPaths.isInside(base, Path.of("/etc/passwd"))).isFalse();
        // 이름 앞부분만 같은 형제 디렉터리는 바깥이다
        assertThat(UploadPaths.isInside(base, tempDir.resolve("static/verification-files-evil/a.pdf"))).isFalse();
    }

    @Test
    void storesUploadUnderAGeneratedNameInsideTheBaseDirectory() throws IOException {
        Path base = tempDir.resolve("uploads/image"); // 없는 디렉터리는 만든다
        MockMultipartFile file = new MockMultipartFile("image", "../../../evil.PNG", "image/png", bytes("png-bytes"));

        String stored = UploadPaths.store(file, base, UploadPaths.IMAGE_EXTENSIONS);

        assertThat(stored).matches(UUID_NAME + "\\.png");
        assertThat(Files.readString(base.resolve(stored))).isEqualTo("png-bytes");
        assertThat(filesUnder(tempDir)).containsExactly(base.resolve(stored));
    }

    @Test
    void storesNothingWhenTheUploadIsRejected() throws IOException {
        Path base = tempDir.resolve("image");

        for (MockMultipartFile file : List.of(
                new MockMultipartFile("image", "../../evil.jsp", "image/png", bytes("x")),
                new MockMultipartFile("image", "noextension", "image/png", bytes("x")),
                new MockMultipartFile("image", null, "image/png", bytes("x")),
                new MockMultipartFile("image", "empty.png", "image/png", new byte[0]))) {
            assertThatThrownBy(() -> UploadPaths.store(file, base, UploadPaths.IMAGE_EXTENSIONS))
                    .as(file.getOriginalFilename())
                    .isInstanceOf(UploadPaths.InvalidUploadException.class);
        }
        assertThatThrownBy(() -> UploadPaths.store(null, base, UploadPaths.IMAGE_EXTENSIONS))
                .isInstanceOf(UploadPaths.InvalidUploadException.class);
        assertThat(filesUnder(tempDir)).isEmpty();
    }

    private static byte[] bytes(String text) {
        return text.getBytes(StandardCharsets.UTF_8);
    }

    private static List<Path> filesUnder(Path dir) throws IOException {
        try (Stream<Path> paths = Files.walk(dir)) {
            return paths.filter(Files::isRegularFile).toList();
        }
    }
}
