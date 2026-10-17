package proj.travien.service;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.data.domain.PageRequest;
import org.springframework.security.crypto.bcrypt.BCrypt;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;
import proj.travien.domain.PointsHistory;
import proj.travien.domain.Role;
import proj.travien.domain.User;
import proj.travien.dto.AgentApplicationDTO;
import proj.travien.dto.UserDTO;
import proj.travien.dto.AgentDTO;
import proj.travien.repository.PointsHistoryRepository;
import proj.travien.repository.UserRepository;

import java.io.FileNotFoundException;
import java.io.IOException;
import java.nio.file.InvalidPathException;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.stream.Collectors;

@Service
public class UserService {

    private final UserRepository userRepository;

    private final PointsHistoryRepository pointsHistoryRepository;

    // 업로드 파일 기준 경로 (static/profile-pictures, static/verification-files 가 이 아래에 있다)
    private Path uploadRoot = Paths.get("src/main/resources");

    private static final Path PROFILE_PICTURE_DIR = Paths.get("static/profile-pictures");
    private static final Path VERIFICATION_FILE_DIR = Paths.get("static/verification-files");

    @Autowired
    public UserService(UserRepository userRepository, PointsHistoryRepository pointsHistoryRepository) {
        this.userRepository = userRepository;
        this.pointsHistoryRepository = pointsHistoryRepository;
    }


    /**
     * 나은이가 추가
     */
    public void save(User user) {
        userRepository.save(user);  // 사용자 정보 저장
    }

    //BookReview에 사용되는 Service
    public User getUserByNickname(String nickname) {
        return userRepository.findByNickname(nickname)
                .orElseThrow(() -> new IllegalArgumentException("User not found with nickname: " + nickname));
    }


    // 회원가입 시 기본 사용자로만 등록
    public boolean createUser(UserDTO userDTO, MultipartFile profilePictureFile, MultipartFile verificationFile) {
        if (isUserIdInUse(userDTO.getUserId()) || isNicknameInUse(userDTO.getNickname())) {
            return false;
        }

        User user = new User();
        user.setUserId(userDTO.getUserId());
        user.setPassword(hashPassword(userDTO.getPassword()));
        user.setName(userDTO.getName());
        user.setDateOfBirth(userDTO.getDateOfBirth());
        user.setGender(userDTO.getGender());
        user.setMbti(userDTO.getMbti());
        user.setNickname(userDTO.getNickname());
        user.setAgent(false); // 기본 사용자는 예약대행자가 아님
        user.setRole(Role.ROLE_USER);
        user.setPoints(1000);

        // 프로필 사진 저장
        if (profilePictureFile != null && !profilePictureFile.isEmpty()) {
            String profilePicturePath = saveProfilePicture(profilePictureFile);
            user.setProfilePicture(profilePicturePath);
        }

        userRepository.save(user);
        return true;
    }

    // 예약대행자 신청
    public boolean applyAsAgent(String userId, AgentApplicationDTO agentDTO, MultipartFile verificationFile) {
        User user = userRepository.findByUserId(userId).orElse(null);
        if (user == null || user.isAgent()) {
            return false;
        }

        // 검증 파일을 먼저 저장 (허용되지 않는 파일이면 사용자 정보를 바꾸기 전에 거절)
        String verificationFilePath = null;
        if (verificationFile != null && !verificationFile.isEmpty()) {
            verificationFilePath = saveVerificationFile(verificationFile);
        }

        user.setAgent(true); // 예약대행자로 설정
        user.setAgentCountry(agentDTO.getAgentCountry());
        user.setIntroduction(agentDTO.getIntroduction());
        user.setHashtags(agentDTO.getHashtags());
        user.setSpecIntroduction(agentDTO.getSpecIntroduction());
        user.setAverageReviewRating(agentDTO.getAverageReviewRating());
        user.setRole(Role.ROLE_AGENT);

        if (verificationFilePath != null) {
            user.setVerificationFile(verificationFilePath);
        }

        userRepository.save(user);
        return true;
    }

    public boolean approveAgentApplication(String userId) {
        User user = userRepository.findByUserId(userId).orElse(null);
        if (user == null || !user.isAgent()) {
            return false;
        }

        // 역할을 예약대행자로 설정하고 승인 날짜를 현재 시점으로 설정
        user.setRole(Role.ROLE_AGENT);
        user.setAgentApprovedDate(LocalDateTime.now()); // 현재 시점을 저장
        userRepository.save(user);
        return true;
    }

    public List<Map<String, Object>> getRecentAgents(int limit) {
        return userRepository.findByIsAgentTrueOrderByAgentApprovedDateDesc(PageRequest.of(0, limit))
                .stream()
                .map(user -> Map.of(
                        "nickname", user.getNickname(),
                        "profilePicture", user.getProfilePicture(),
                        "hashtags", user.getHashtags(),
                        "agentCountry", user.getAgentCountry()
                ))
                .collect(Collectors.toList());
    }

    // 프로필 사진 업로드
    public boolean uploadProfilePicture(String userId, MultipartFile profilePictureFile) {
        User user = userRepository.findByUserId(userId).orElse(null);
        if (user == null) {
            return false;
        }

        String profilePicturePath = saveProfilePicture(profilePictureFile);
        user.setProfilePicture(profilePicturePath);
        userRepository.save(user);
        return true;
    }

    // 검증 파일 업로드
    public boolean uploadVerificationFile(String userId, MultipartFile verificationFile) {
        User user = userRepository.findByUserId(userId).orElse(null);
        if (user == null) {
            return false;
        }

        String verificationFilePath = saveVerificationFile(verificationFile);
        user.setVerificationFile(verificationFilePath);
        userRepository.save(user);
        return true;
    }

    public String getProfilePicturePath(String nickname) {
        User user = userRepository.findByNickname(nickname)
                .orElseThrow(() -> new RuntimeException("User not found with nickname: " + nickname));
        return user.getProfilePicture();
    }

    public String getProfilePictureByNickname(String nickname) {
        User user = userRepository.findByNickname(nickname)
                .orElseThrow(() -> new RuntimeException("User not found"));
        return user.getProfilePicture(); // profilePicture 필드가 파일 경로를 저장한다고 가정
    }

    // 검증 파일 로드
    public Resource loadVerificationFile(String userId) throws IOException {
        User user = userRepository.findByUserId(userId).orElse(null);
        if (user == null || user.getVerificationFile() == null) {
            throw new FileNotFoundException("Verification file not found for user: " + userId);
        }
        // 저장된 경로가 검증 파일 디렉터리를 벗어나면 읽지 않는다
        Path file;
        try {
            file = uploadRoot.resolve(user.getVerificationFile());
        } catch (InvalidPathException e) {
            throw new FileNotFoundException("Verification file not found for user: " + userId);
        }
        if (!UploadPaths.isInside(uploadRoot.resolve(VERIFICATION_FILE_DIR), file)) {
            throw new FileNotFoundException("Verification file not found for user: " + userId);
        }
        return new FileSystemResource(file.toAbsolutePath().normalize().toString());
    }

    // 파일 저장 (클라이언트 파일명은 확장자 확인에만 쓰고, 저장 이름은 서버에서 만든다)
    private String saveFile(MultipartFile file, Path directory, Set<String> allowedExtensions) {
        try {
            Path absoluteDirectory = uploadRoot.resolve(directory).toAbsolutePath();
            String filename = UploadPaths.store(file, absoluteDirectory, allowedExtensions);
            return directory.resolve(filename).toString(); // 상대 경로 저장
        } catch (IOException e) {
            throw new RuntimeException("Failed to save file", e);
        }
    }

    // 프로필 사진 저장
    private String saveProfilePicture(MultipartFile profilePictureFile) {
        return saveFile(profilePictureFile, PROFILE_PICTURE_DIR, UploadPaths.IMAGE_EXTENSIONS);
    }

    // 검증 파일 저장
    private String saveVerificationFile(MultipartFile verificationFile) {
        return saveFile(verificationFile, VERIFICATION_FILE_DIR, UploadPaths.DOCUMENT_EXTENSIONS);
    }

    // 테스트에서 업로드 기준 경로를 임시 디렉터리로 바꿀 때 사용
    void setUploadRoot(Path uploadRoot) {
        this.uploadRoot = uploadRoot;
    }

    // 유저 ID 사용 여부 확인
    public boolean isUserIdInUse(String userId) {
        return userRepository.findByUserId(userId).isPresent();
    }

    // 닉네임 사용 여부 확인
    public boolean isNicknameInUse(String nickname) {
        return userRepository.findByNickname(nickname).isPresent();
    }

    // 비밀번호 해시
    private String hashPassword(String password) {
        return BCrypt.hashpw(password, BCrypt.gensalt());
    }

    // 로그인
    public User login(String userId, String password) {
        User user = userRepository.findByUserId(userId).orElse(null);
        if (user != null && BCrypt.checkpw(password, user.getPassword())) {
            return user;
        }
        return null;
    }

    // 모든 닉네임 조회
    public List<String> getAllNicknames() {
        return userRepository.findAll().stream()
                .map(User::getNickname)
                .collect(Collectors.toList());
    }

    // 유저 ID로 사용자 찾기
    public User findByUserId(String userId) {
        return userRepository.findByUserId(userId).orElse(null);
    }

    // 프로필 수정 메서드
    public boolean updateUserProfile(String userId, UserDTO userDTO) {
        User user = userRepository.findByUserId(userId).orElse(null);
        if (user == null) {
            return false;
        }

        // ID를 제외한 모든 필드를 수정
        user.setPassword(userDTO.getPassword() != null ? hashPassword(userDTO.getPassword()) : user.getPassword());
        user.setName(userDTO.getName());
        user.setDateOfBirth(userDTO.getDateOfBirth());
        user.setGender(userDTO.getGender());
        user.setMbti(userDTO.getMbti());
        user.setProfilePicture(userDTO.getProfilePicture());
        user.setNickname(userDTO.getNickname());
        user.setStatusMessage(userDTO.getStatusMessage()); // 상태 메시지 추가

        // 포인트는 수정하지 않음
        // user.setPoints(userDTO.getPoints());

        userRepository.save(user);

        return true;
    }

    // 예약대행자 정보 업데이트
    public boolean updateAgentDetails(String userId, AgentDTO agentDTO) {
        User user = userRepository.findByUserId(userId).orElse(null);
        if (user == null || !user.isAgent()) {
            return false;
        }

        user.setAgentCountry(agentDTO.getAgentCountry());
        user.setIntroduction(agentDTO.getIntroduction());
        user.setHashtags(agentDTO.getHashtags());
        user.setSpecIntroduction(agentDTO.getSpecIntroduction());
        user.setAverageReviewRating(agentDTO.getAverageReviewRating());

        userRepository.save(user);
        return true;
    }

    // 예약대행자 목록 조회
    public List<AgentDTO> getAllAgents() {
        return userRepository.findByIsAgentTrue().stream()
                .map(user -> new AgentDTO(
                        user.getAgentCountry(),
                        user.getIntroduction(),
                        user.getHashtags(),
                        user.getSpecIntroduction(),
                        user.getAverageReviewRating(),
                        user.getNickname(),           // 닉네임 추가
                        user.getProfilePicture()      // 프로필 이미지 추가
                ))
                .collect(Collectors.toList());
    }

    // 해시태그로 예약대행자 검색
    public List<AgentDTO> searchAgentsByHashtag(String hashtag) {
        return userRepository.findByIsAgentTrue().stream()
                .filter(user -> user.getHashtags() != null && user.getHashtags().contains(hashtag))
                .map(user -> new AgentDTO(
                        user.getAgentCountry(),
                        user.getIntroduction(),
                        user.getHashtags(),
                        user.getSpecIntroduction(),
                        user.getAverageReviewRating(),
                        user.getNickname(),
                        user.getProfilePicture()
                ))
                .collect(Collectors.toList());
    }

    // 특정 국가의 예약대행자 목록 조회
    public List<AgentDTO> getAgentsByCountry(String country) {
        return userRepository.findByIsAgentTrueAndAgentCountry(country).stream()
                .map(user -> new AgentDTO(
                        user.getAgentCountry(),
                        user.getIntroduction(),
                        user.getHashtags(),
                        user.getSpecIntroduction(),
                        user.getAverageReviewRating(),
                        user.getNickname(),           // 닉네임 추가
                        user.getProfilePicture()      // 프로필 이미지 추가
                ))
                .collect(Collectors.toList());
    }

    public UserDTO createUserDTO(User user) {
        UserDTO userDTO = new UserDTO(
                user.getUserId(),
                null, // 비밀번호는 반환하지 않음
                user.getName(),
                user.getDateOfBirth(),
                user.getGender(),
                user.getMbti(),
                user.getProfilePicture(),
                user.getNickname(),
                user.isAgent(),
                user.getPoints(), // 포인트 필드 추가
                user.getStatusMessage() // 상태 메시지 추가
        );

        if (user.isAgent()) {
            userDTO.setAgentDetails(createAgentDTO(user));
        }
        return userDTO;
    }

    // 공통된 AgentDTO 생성 로직을 메소드로 추출
    public AgentDTO createAgentDTO(User user) {
        return new AgentDTO(
                user.getAgentCountry(),
                user.getIntroduction(),
                user.getHashtags(),
                user.getSpecIntroduction(),
                user.getAverageReviewRating(),
                user.getNickname(),
                user.getProfilePicture()
        );
    }

    // 마이페이지 정보 조회 메서드
    public UserDTO getUserInfo(String userId) {
        User user = userRepository.findByUserId(userId).orElse(null);
        if (user == null) {
            return null;
        }

        return createUserDTO(user);
    }

    // 포인트 조회
    public int getUserPoints(String userId) {
        User user = userRepository.findByUserId(userId).orElse(null);
        if (user == null) {
            throw new IllegalArgumentException("User not found");
        }
        return user.getPoints();
    }


    // 포인트 추가
    @Transactional
    public boolean addPointsByNickname(String nickname, int pointsToAdd) {
        User user = userRepository.findByNickname(nickname).orElse(null);
        if (user == null) {
            throw new IllegalArgumentException("User not found");
        }

        if (pointsToAdd <= 0) {
            throw new IllegalArgumentException("Points to add must be positive");
        }

        user.setPoints(user.getPoints() + pointsToAdd); // 포인트 추가
        userRepository.save(user);

        // 포인트 히스토리 저장
        PointsHistory history = new PointsHistory(nickname, pointsToAdd, "add");
        pointsHistoryRepository.save(history);

        return true;
    }

    // 포인트 차감
    @Transactional
    public boolean deductPointsByNickname(String nickname, int pointsToDeduct) {
        User user = userRepository.findByNickname(nickname).orElse(null);
        if (user == null) {
            throw new IllegalArgumentException("User not found");
        }

        if (pointsToDeduct <= 0) {
            throw new IllegalArgumentException("Points to deduct must be positive");
        }

        if (user.getPoints() < pointsToDeduct) {
            throw new IllegalArgumentException("Insufficient points");
        }

        user.setPoints(user.getPoints() - pointsToDeduct); // 포인트 차감
        userRepository.save(user);

        // 포인트 히스토리 저장
        PointsHistory history = new PointsHistory(nickname, pointsToDeduct, "deduct");
        pointsHistoryRepository.save(history);

        return true;
    }


    //관리자 포인트 조정
    public boolean adjustPoints(String userId, int points) {
        User user = userRepository.findByUserId(userId).orElseThrow(() -> new IllegalArgumentException("User not found"));
        user.setPoints(user.getPoints() + points);
        userRepository.save(user);
        return true;
    }

    // 관리자 승격
    public void promoteToAdmin(String userId) {
        User user = userRepository.findByUserId(userId).orElseThrow(() -> new IllegalArgumentException("User not found"));

        // 기존 역할을 덮어쓰고 ROLE_ADMIN으로 설정
        user.setRole(Role.ROLE_ADMIN);

        userRepository.save(user);
    }

    //모든 사용자 조회
    public List<UserDTO> getAllUsers() {
        return userRepository.findAll().stream()
                .map(this::createUserDTO)
                .collect(Collectors.toList());
    }

    //사용자 삭제
    public void deleteUser(String userId) {
        User user = userRepository.findByUserId(userId).orElseThrow(() -> new IllegalArgumentException("User not found"));
        userRepository.delete(user);
    }

    public Long getIdByNickname(String nickname){
        Optional<User> byNickname = userRepository.findByNickname(nickname);
        return byNickname.get().getId();
    }
}
