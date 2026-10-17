package proj.travien;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.CommandLineRunner;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;
import proj.travien.domain.Role;
import proj.travien.domain.User;
import proj.travien.repository.UserRepository;

import java.util.Optional;

/**
 * 서버 시작 시 admin 관리자 계정의 비밀번호를 설정값 GLOPLE_ADMIN_PASSWORD(프로젝트 루트 .env 또는 환경 변수)에 맞춘다.
 * 비밀번호는 코드에 두지 않고, 로그에도 남기지 않는다.
 */
@Slf4j
@Component
public class AdminAccountInitializer implements CommandLineRunner {

    static final String ADMIN_USER_ID = "admin";

    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;
    private final String adminPassword;

    @Autowired
    public AdminAccountInitializer(
            UserRepository userRepository,
            PasswordEncoder passwordEncoder,
            @Value("${GLOPLE_ADMIN_PASSWORD:}") String adminPassword
    ) {
        this.userRepository = userRepository;
        this.passwordEncoder = passwordEncoder;
        this.adminPassword = adminPassword;
    }

    @Override
    public void run(String... args) {
        // 설정값이 없으면 계정을 만들지도, 기존 비밀번호를 바꾸지도 않는다.
        if (adminPassword == null || adminPassword.isBlank()) {
            log.warn("GLOPLE_ADMIN_PASSWORD가 설정되지 않아 관리자 계정({})을 만들거나 갱신하지 않습니다. "
                    + "이미 있는 관리자 계정은 기존 비밀번호를 그대로 씁니다. "
                    + "프로젝트 루트 .env에 GLOPLE_ADMIN_PASSWORD를 넣고(.env.example 참고) 서버를 다시 시작하세요.", ADMIN_USER_ID);
            return;
        }

        Optional<User> existing = userRepository.findByUserId(ADMIN_USER_ID);
        if (existing.isEmpty()) {
            User admin = new User();
            admin.setUserId(ADMIN_USER_ID);
            admin.setPassword(passwordEncoder.encode(adminPassword));
            admin.setName("Administrator");
            admin.setNickname("admin");
            admin.setRole(Role.ROLE_ADMIN);
            admin.setPoints(1000);

            userRepository.save(admin);
            log.info("관리자 계정({})을 만들었습니다. 비밀번호는 GLOPLE_ADMIN_PASSWORD 값입니다.", ADMIN_USER_ID);
            return;
        }

        // 저장된 해시가 설정값과 다르면(예전 공개 기본 비밀번호 등) 설정값으로 바꾼다.
        User admin = existing.get();
        String storedHash = admin.getPassword();
        if (storedHash == null || storedHash.isEmpty() || !passwordEncoder.matches(adminPassword, storedHash)) {
            admin.setPassword(passwordEncoder.encode(adminPassword));
            userRepository.save(admin);
            log.info("관리자 계정({})의 비밀번호를 GLOPLE_ADMIN_PASSWORD 값으로 바꿨습니다.", ADMIN_USER_ID);
        }
    }
}
