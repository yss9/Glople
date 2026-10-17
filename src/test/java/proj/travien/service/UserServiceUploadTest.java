package proj.travien.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.core.io.Resource;
import org.springframework.mock.web.MockMultipartFile;
import proj.travien.domain.User;
import proj.travien.dto.AgentApplicationDTO;
import proj.travien.dto.UserDTO;
import proj.travien.repository.PointsHistoryRepository;
import proj.travien.repository.UserRepository;

import java.io.FileNotFoundException;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Optional;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.argThat;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;

// DB 없이 저장소를 목으로 두고, 업로드 파일은 임시 디렉터리에 저장한다.
class UserServiceUploadTest {

    private static final String UUID_NAME = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";

    @TempDir
    Path tempDir;

    private Path uploadRoot;
    private final UserRepository userRepository = mock(UserRepository.class);
    private final UserService userService = new UserService(userRepository, mock(PointsHistoryRepository.class));
    private final User user = new User();

    @BeforeEach
    void setUp() {
        uploadRoot = tempDir.resolve("resources");
        userService.setUploadRoot(uploadRoot);
        user.setUserId("tester");
        given(userRepository.findByUserId("tester")).willReturn(Optional.of(user));
    }

    @Test
    void storesProfilePictureUnderGeneratedNameInsideProfileDirectory() throws IOException {
        MockMultipartFile picture = file("profilePicture", "../../../../evil.JPG", "jpg-bytes");

        assertThat(userService.uploadProfilePicture("tester", picture)).isTrue();

        // DB에는 기존과 같은 "static/profile-pictures/<이름>" 형식의 상대 경로가 저장된다
        String stored = user.getProfilePicture().replace('\\', '/');
        assertThat(stored).matches("static/profile-pictures/" + UUID_NAME + "\\.jpg");
        assertThat(Files.readString(uploadRoot.resolve(user.getProfilePicture()))).isEqualTo("jpg-bytes");
        assertThat(filesUnder(tempDir)).containsExactly(uploadRoot.resolve(user.getProfilePicture()));
        verify(userRepository).save(user);
    }

    @Test
    void storesSignupProfilePictureWithoutClientName() {
        given(userRepository.findByUserId(anyString())).willReturn(Optional.empty());
        given(userRepository.findByNickname(anyString())).willReturn(Optional.empty());
        UserDTO dto = new UserDTO();
        dto.setUserId("newbie");
        dto.setPassword("pw");
        dto.setNickname("newbie");

        assertThat(userService.createUser(dto, file("profilePicture", "..\\..\\evil.png", "png"), null)).isTrue();

        verify(userRepository).save(argThat(saved ->
                saved.getProfilePicture().replace('\\', '/').matches("static/profile-pictures/" + UUID_NAME + "\\.png")));
    }

    @Test
    void rejectsProfilePictureThatIsNotAnImage() throws IOException {
        assertThatThrownBy(() -> userService.uploadProfilePicture("tester", file("profilePicture", "avatar.svg", "<svg/>")))
                .isInstanceOf(UploadPaths.InvalidUploadException.class);

        assertThat(user.getProfilePicture()).isNull();
        assertThat(filesUnder(tempDir)).isEmpty();
        verify(userRepository, never()).save(any());
    }

    @Test
    void storesVerificationFileInsideVerificationDirectory() throws IOException {
        assertThat(userService.uploadVerificationFile("tester", file("verificationFile", "/etc/../../license.PDF", "pdf"))).isTrue();

        assertThat(user.getVerificationFile().replace('\\', '/')).matches("static/verification-files/" + UUID_NAME + "\\.pdf");
        assertThat(filesUnder(tempDir)).containsExactly(uploadRoot.resolve(user.getVerificationFile()));
    }

    @Test
    void rejectsAgentApplicationWithDangerousVerificationFileBeforeChangingTheUser() throws IOException {
        AgentApplicationDTO dto = new AgentApplicationDTO("Japan", "intro", List.of("#tag"), List.of("spec"), 4.5);

        assertThatThrownBy(() -> userService.applyAsAgent("tester", dto, file("verificationFile", "proof.pdf.html", "<script>")))
                .isInstanceOf(UploadPaths.InvalidUploadException.class);

        assertThat(user.isAgent()).isFalse();
        assertThat(user.getAgentCountry()).isNull();
        assertThat(user.getVerificationFile()).isNull();
        assertThat(filesUnder(tempDir)).isEmpty();
        verify(userRepository, never()).save(any());
    }

    @Test
    void loadsVerificationFileOnlyFromVerificationDirectory() throws IOException {
        Path secret = Files.writeString(Files.createDirectories(uploadRoot).resolve("application.properties"), "secret");
        user.setVerificationFile("static/verification-files/../../application.properties");

        assertThatThrownBy(() -> userService.loadVerificationFile("tester")).isInstanceOf(FileNotFoundException.class);

        user.setVerificationFile(secret.toAbsolutePath().toString());
        assertThatThrownBy(() -> userService.loadVerificationFile("tester")).isInstanceOf(FileNotFoundException.class);

        Path proof = Files.writeString(Files.createDirectories(uploadRoot.resolve("static/verification-files")).resolve("proof.pdf"), "pdf");
        user.setVerificationFile("static/verification-files/proof.pdf");
        Resource resource = userService.loadVerificationFile("tester");
        assertThat(resource.getFile().toPath()).isEqualTo(proof.toAbsolutePath().normalize());
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
