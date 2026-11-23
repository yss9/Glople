package proj.travien.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.ResourceHandlerRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration
public class WebConfig implements WebMvcConfigurer {
    @Override
    public void addResourceHandlers(ResourceHandlerRegistry registry) {
        registry.addResourceHandler("/image/**")
                .addResourceLocations("file:src/main/resources/static/image/");
        registry.addResourceHandler("/profile-pictures/**")
                .addResourceLocations("file:src/main/resources/static/profile-pictures/");
        // 검증 서류(/verification-files/**)는 정적 경로로 내보내지 않는다.
        // 본인·관리자만 GET /api/users/verification-file/{userId}로 받는다. (SecurityConfig가 정적 경로를 차단)
    }
}
