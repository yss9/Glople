package proj.travien.service;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.InvalidPathException;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Pattern;

/**
 * 업로드 파일 저장 경로 도우미.
 * 클라이언트가 보낸 파일 이름은 확장자를 고르는 데만 쓰고, 실제 저장 이름은 서버가 UUID로 만든다.
 * 저장 위치는 기준 디렉터리 바로 아래로만 허용한다. (../, ..\, 절대 경로, 인코딩된 경로 조작 차단)
 */
public final class UploadPaths {

    /** 게시글/에디터/프로필 사진에 허용하는 이미지 확장자 */
    public static final Set<String> IMAGE_EXTENSIONS = Set.of("jpg", "jpeg", "png", "gif", "webp", "bmp");

    /** 예약대행자 검증 서류, 채팅 첨부 파일에 허용하는 확장자 (이미지 + 문서). html, svg, js 같은 스크립트 실행 가능 형식은 제외 */
    public static final Set<String> DOCUMENT_EXTENSIONS = union(IMAGE_EXTENSIONS,
            Set.of("pdf", "txt", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "hwp", "hwpx"));

    // 확장자는 영문 소문자/숫자 10자 이내만 인정
    private static final Pattern EXTENSION = Pattern.compile("[a-z0-9]{1,10}");

    // 서버가 만든 저장 이름 형식 (경로 구분자, 공백, 앞쪽 점 불가)
    private static final Pattern STORED_NAME = Pattern.compile("[A-Za-z0-9][A-Za-z0-9._-]{0,254}");

    private UploadPaths() {
    }

    /**
     * 업로드 파일을 기준 디렉터리에 새 이름으로 저장하고, 저장한 파일 이름을 돌려준다.
     * 같은 이름의 파일이 이미 있으면 덮어쓰지 않고 실패한다.
     */
    public static String store(MultipartFile file, Path baseDir, Set<String> allowedExtensions) throws IOException {
        if (file == null || file.isEmpty()) {
            throw new InvalidUploadException("업로드할 파일이 없습니다.");
        }
        String fileName = newFileName(file.getOriginalFilename(), allowedExtensions);
        Path target = resolveInside(baseDir, fileName);
        Files.createDirectories(target.getParent());
        try (InputStream in = file.getInputStream()) {
            Files.copy(in, target);
        }
        return fileName;
    }

    /** 원본 파일 이름의 확장자만 살려 "UUID.확장자" 형태의 저장 이름을 만든다. */
    public static String newFileName(String originalFilename, Set<String> allowedExtensions) {
        return UUID.randomUUID() + "." + extensionOf(originalFilename, allowedExtensions);
    }

    /**
     * 원본 파일 이름에서 허용된 확장자를 소문자로 꺼낸다.
     * 경로 구분자 뒤의 마지막 부분만 보고, 마지막 점 뒤를 확장자로 본다. (photo.php.png -> png, photo.png.php -> 거절)
     */
    public static String extensionOf(String originalFilename, Set<String> allowedExtensions) {
        if (originalFilename == null || originalFilename.isBlank()) {
            throw new InvalidUploadException("파일 이름이 없습니다.");
        }
        String name = originalFilename.trim();
        int separator = Math.max(name.lastIndexOf('/'), name.lastIndexOf('\\'));
        name = name.substring(separator + 1);

        int dot = name.lastIndexOf('.');
        if (dot <= 0 || dot == name.length() - 1) {
            throw new InvalidUploadException("확장자가 없는 파일입니다.");
        }
        String extension = name.substring(dot + 1).toLowerCase(Locale.ROOT);
        if (!EXTENSION.matcher(extension).matches() || !allowedExtensions.contains(extension)) {
            throw new InvalidUploadException("허용되지 않는 파일 형식입니다.");
        }
        return extension;
    }

    /** 기준 디렉터리 바로 아래의 파일 경로를 만든다. 디렉터리 밖으로 나가거나 하위 경로가 섞인 이름은 거절한다. */
    public static Path resolveInside(Path baseDir, String fileName) {
        if (fileName == null || !STORED_NAME.matcher(fileName).matches()) {
            throw new InvalidUploadException("허용되지 않는 파일 이름입니다.");
        }
        Path base = baseDir.toAbsolutePath().normalize();
        Path target;
        try {
            target = base.resolve(fileName).normalize();
        } catch (InvalidPathException e) {
            throw new InvalidUploadException("허용되지 않는 파일 이름입니다.");
        }
        if (!base.equals(target.getParent())) {
            throw new InvalidUploadException("업로드 디렉터리를 벗어난 경로입니다.");
        }
        return target;
    }

    /** candidate 가 baseDir 안쪽(baseDir 자신 제외)에 있는지 확인한다. DB에 저장된 기존 경로를 읽을 때 사용 */
    public static boolean isInside(Path baseDir, Path candidate) {
        Path base = baseDir.toAbsolutePath().normalize();
        Path target = candidate.toAbsolutePath().normalize();
        return !target.equals(base) && target.startsWith(base);
    }

    private static Set<String> union(Set<String> first, Set<String> second) {
        Set<String> all = new HashSet<>(first);
        all.addAll(second);
        return Set.copyOf(all);
    }

    /** 허용되지 않는 업로드. 컨트롤러에서 잡지 않으면 400으로 응답한다. */
    @ResponseStatus(HttpStatus.BAD_REQUEST)
    public static class InvalidUploadException extends IllegalArgumentException {
        public InvalidUploadException(String message) {
            super(message);
        }
    }
}
