package proj.travien.service;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.nio.file.Path;
import java.nio.file.Paths;

@Service
@Slf4j
public class FileUploadeService {

    @Value("${upload.location}")
    private String UPLOAD_LOCATION;

    public String saveFile(MultipartFile file) {
        // 이미지 확장자만 허용하고, 저장 이름은 UUID.확장자 로 서버에서 만든다
        String saveFileName = UploadPaths.newFileName(file.getOriginalFilename(), UploadPaths.IMAGE_EXTENSIONS);
        Path savePath = UploadPaths.resolveInside(Paths.get(UPLOAD_LOCATION), saveFileName);

        try {
            file.transferTo(savePath);
        } catch (IOException e) {
            log.error("Error occurred while saving file: {}", e.getMessage(), e);
        }
        return saveFileName;
    }
}
