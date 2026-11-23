package proj.travien;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.slf4j.LoggerFactory;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import proj.travien.domain.Role;
import proj.travien.domain.User;
import proj.travien.repository.UserRepository;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class AdminAccountInitializerTest {

    private static final String CONFIGURED_PASSWORD = "configured-Admin-Pw-42";

    private final UserRepository userRepository = mock(UserRepository.class);
    private final PasswordEncoder passwordEncoder = mock(PasswordEncoder.class);

    private final Logger logger = (Logger) LoggerFactory.getLogger(AdminAccountInitializer.class);
    private final ListAppender<ILoggingEvent> logs = new ListAppender<>();

    @BeforeEach
    void attachLogs() {
        logs.start();
        logger.addAppender(logs);
    }

    @AfterEach
    void detachLogs() {
        logger.detachAppender(logs);
    }

    @Test
    void skipsAdminWhenPasswordIsNotConfigured() {
        // (a) 설정값이 비어 있으면 계정을 만들지 않고 경고만 남긴다.
        new AdminAccountInitializer(userRepository, passwordEncoder, "").run();
        new AdminAccountInitializer(userRepository, passwordEncoder, "   ").run();
        new AdminAccountInitializer(userRepository, passwordEncoder, null).run();

        verifyNoInteractions(userRepository, passwordEncoder);
        assertThat(logs.list).hasSize(3).allSatisfy(event -> {
            assertThat(event.getLevel()).isEqualTo(Level.WARN);
            assertThat(event.getFormattedMessage()).contains("GLOPLE_ADMIN_PASSWORD", ".env");
        });
    }

    @Test
    void createsAdminWithEncodedConfiguredPassword() {
        // (b) 설정값이 있고 admin이 없으면 PasswordEncoder로 인코딩해 만든다.
        when(userRepository.findByUserId("admin")).thenReturn(Optional.empty());
        when(passwordEncoder.encode(CONFIGURED_PASSWORD)).thenReturn("encoded-hash");

        new AdminAccountInitializer(userRepository, passwordEncoder, CONFIGURED_PASSWORD).run();

        ArgumentCaptor<User> saved = ArgumentCaptor.forClass(User.class);
        verify(userRepository).save(saved.capture());
        assertThat(saved.getValue().getUserId()).isEqualTo("admin");
        assertThat(saved.getValue().getPassword()).isEqualTo("encoded-hash");
        assertThat(saved.getValue().getRole()).isEqualTo(Role.ROLE_ADMIN);
        assertThat(saved.getValue().getNickname()).isEqualTo("admin");
        assertThat(saved.getValue().getPoints()).isEqualTo(1000);
        assertNoLogContainsPassword();
    }

    @Test
    void updatesExistingAdminWhoseHashDoesNotMatch() {
        // (c) 기존 admin의 해시가 설정값과 다르면 설정값으로 바꾼다.
        User admin = existingAdmin("old-public-hash");
        when(userRepository.findByUserId("admin")).thenReturn(Optional.of(admin));
        when(passwordEncoder.matches(CONFIGURED_PASSWORD, "old-public-hash")).thenReturn(false);
        when(passwordEncoder.encode(CONFIGURED_PASSWORD)).thenReturn("new-hash");

        new AdminAccountInitializer(userRepository, passwordEncoder, CONFIGURED_PASSWORD).run();

        verify(userRepository).save(admin);
        assertThat(admin.getPassword()).isEqualTo("new-hash");
        assertThat(admin.getRole()).isEqualTo(Role.ROLE_ADMIN);
        assertThat(logs.list).anySatisfy(event ->
                assertThat(event.getFormattedMessage()).contains("비밀번호를 GLOPLE_ADMIN_PASSWORD 값으로 바꿨습니다"));
        assertNoLogContainsPassword();
    }

    @Test
    void updatesExistingAdminWithoutPasswordHash() {
        User admin = existingAdmin(null);
        when(userRepository.findByUserId("admin")).thenReturn(Optional.of(admin));
        when(passwordEncoder.encode(CONFIGURED_PASSWORD)).thenReturn("new-hash");

        new AdminAccountInitializer(userRepository, passwordEncoder, CONFIGURED_PASSWORD).run();

        verify(passwordEncoder, never()).matches(any(), any());
        verify(userRepository).save(admin);
        assertThat(admin.getPassword()).isEqualTo("new-hash");
    }

    @Test
    void keepsExistingAdminWhoseHashAlreadyMatches() {
        // 이미 설정값과 같은 비밀번호면 다시 저장하지 않는다.
        User admin = existingAdmin("current-hash");
        when(userRepository.findByUserId("admin")).thenReturn(Optional.of(admin));
        when(passwordEncoder.matches(CONFIGURED_PASSWORD, "current-hash")).thenReturn(true);

        new AdminAccountInitializer(userRepository, passwordEncoder, CONFIGURED_PASSWORD).run();

        verify(userRepository, never()).save(any());
        verify(passwordEncoder, never()).encode(anyString());
        assertThat(admin.getPassword()).isEqualTo("current-hash");
        assertThat(logs.list).isEmpty();
    }

    @Test
    void replacedHashAcceptsOnlyConfiguredPasswordWithRealBCrypt() {
        // 실제 BCrypt로 바꾼 뒤에는 새 설정값만 맞고 예전 비밀번호는 맞지 않는다.
        BCryptPasswordEncoder bcrypt = new BCryptPasswordEncoder();
        User admin = existingAdmin(bcrypt.encode("previous-default"));
        when(userRepository.findByUserId("admin")).thenReturn(Optional.of(admin));

        new AdminAccountInitializer(userRepository, bcrypt, CONFIGURED_PASSWORD).run();

        verify(userRepository).save(admin);
        assertThat(admin.getPassword()).doesNotContain(CONFIGURED_PASSWORD);
        assertThat(bcrypt.matches(CONFIGURED_PASSWORD, admin.getPassword())).isTrue();
        assertThat(bcrypt.matches("previous-default", admin.getPassword())).isFalse();
        assertNoLogContainsPassword();
    }

    private static User existingAdmin(String passwordHash) {
        User admin = new User();
        admin.setUserId("admin");
        admin.setPassword(passwordHash);
        admin.setRole(Role.ROLE_ADMIN);
        return admin;
    }

    private void assertNoLogContainsPassword() {
        assertThat(logs.list).isNotEmpty().allSatisfy(event ->
                assertThat(event.getFormattedMessage()).doesNotContain(CONFIGURED_PASSWORD));
    }
}
