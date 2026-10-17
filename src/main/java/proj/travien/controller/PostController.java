package proj.travien.controller;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
import proj.travien.domain.Addresses;
import proj.travien.domain.Post;
import proj.travien.domain.User;
import proj.travien.dto.AddressResponseDto;
import proj.travien.service.PostService;
import proj.travien.service.UploadPaths;
import proj.travien.service.UserService;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.*;
import java.util.stream.Collectors;

@RestController
@RequestMapping("/api/posts")
@CrossOrigin(origins = "http://localhost:3000")
public class PostController {

    @Autowired
    private PostService postService;
    @Autowired
    private UserService userService;  // 새로 추가된 UserService


    // 업로드 저장 위치 (/image/** 로 서빙되는 디렉터리)
    @Value("${upload.location:src/main/resources/static/image/}")
    private String uploadDir;


    @PostMapping("/upload")
    public String uploadImage(@RequestParam("image") MultipartFile file) {
        if (file.isEmpty()) {
            return "파일이 없습니다.";
        }

        try {
            // 클라이언트 파일명 대신 서버에서 만든 이름(UUID.확장자)으로 저장
            String fileName = UploadPaths.store(file, Paths.get(uploadDir), UploadPaths.IMAGE_EXTENSIONS);

            // 서버에 저장된 파일의 접근 경로를 반환
            return "/image/" + fileName;
        } catch (UploadPaths.InvalidUploadException e) {
            return "허용되지 않는 파일입니다.";
        } catch (IOException e) {
            e.printStackTrace();
            return "파일 업로드 실패";
        }
    }


    /**
     * 게시물 업로드
     */
    @PostMapping("/")
    public ResponseEntity<Post> createPost(@RequestParam("title") String title,
                                           @RequestParam("body") String body,
                                           @RequestParam("addresses") Set<String> addresses,
                                           @RequestParam("addressTitle") String addressTitle,
                                           @RequestParam("country") String country,
                                           @RequestParam("userId") String userId) {

        // 사용자 조회 로직 추가
        User user = userService.findByUserId(userId);  // 사용자 정보를 UserService에서 조회

        if (user == null) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).build();  // 사용자 없는 경우 처리
        }

        // 게시물 생성 로직 (기존 코드 유지)
        Set<Addresses> addressEntities = addresses.stream()
                .map(address -> Addresses.builder().address(address).build())
                .collect(Collectors.toSet());

        // User의 nickname과 mbti 추가
        Post createdPost = postService.createPost(title, body, addressEntities, addressTitle, country);
        createdPost.setNickname(user.getNickname());  // 추가
        createdPost.setMbti(user.getMbti());          // 추가
        createdPost.setProfilePicture(user.getProfilePicture());
        // 포인트 업데이트 추가
        user.setPoints(user.getPoints() + 10);  // 사용자 포인트 +10
        userService.save(user);  // 사용자 정보 업데이트

        return ResponseEntity.status(HttpStatus.CREATED).body(createdPost);  // 생성된 게시물 응답
    }


    /**
     * 루트 추천 (특정 placename에 맞는 포스트 검색)
     */
    @GetMapping("/addresses/{placename}")
    public ResponseEntity<List<AddressResponseDto>> getPostAddressesByPlaceName(@PathVariable String placename) {
        try {
            List<AddressResponseDto> addressResponses = postService.getPostAddressesByPlaceName(placename);
            return ResponseEntity.ok(addressResponses);
        } catch (IllegalStateException e) {
            // '추천되는 게시물 없음' 예외 처리
            return ResponseEntity.status(HttpStatus.NOT_FOUND)
                    .body(Collections.emptyList());
        }
    }


    /**
     * 특정 게시물 가져오기
     */
    @GetMapping("/{id}/")
    public ResponseEntity<Post> getPostById(@PathVariable Long id) {
        Optional<Post> post = postService.getPostById(id);
        return post.map(ResponseEntity::ok).orElseGet(() -> ResponseEntity.status(HttpStatus.NOT_FOUND).build());
    }


    /**
     * 나라별 게시물 목록 가져오기
     */
    @GetMapping("/{country}")
    public ResponseEntity<List<Post>> getPostsByCountry(@PathVariable String country) {
        List<Post> posts = postService.getPostsByCountry(country);
        if (!posts.isEmpty()) {
            return ResponseEntity.ok(posts);
        } else {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).build();
        }
    }


    /**
     * 게시물 전체 가져오기
     */
    @GetMapping("/")
    public List<Post> getAllPosts() {
        return postService.getAllPosts();
    }


    /**
     * 게시물 수정
     */
    @PutMapping("/{boardID}")
    public Object updatePost(@PathVariable Long boardID,
                                           @RequestParam("title") String title,
                                           @RequestParam("body") String body,
                                           @RequestParam("addresses") Set<String> addresses,
                                           @RequestParam("addressTitle") String addressTitle,
                                           @RequestParam("country") String country,
                                           @RequestParam("userId") String userId) {


        // 게시물 수정 로직
        Set<Addresses> addressEntities = addresses.stream()
                .map(address -> Addresses.builder().address(address).build())
                .collect(Collectors.toSet());

        // Post 객체에 사용자 정보 업데이트
        Post updatedPost = new Post();
        updatedPost.setTitle(title);
        updatedPost.setBody(body);
        updatedPost.setAddressTitle(addressTitle);
        updatedPost.setCountry(country);

        try {
            // 게시물 수정 서비스 호출
            Post savedPost = postService.updatePost(boardID, updatedPost, addressEntities);
            return savedPost;  // 수정된 게시물 반환
        } catch (RuntimeException e) {
            System.out.println(e.getMessage());
            return ResponseEntity.status(HttpStatus.NOT_FOUND).build();  // 게시물이 없을 경우 처리
        }
    }


    @DeleteMapping("/{id}/")
    public ResponseEntity<Void> deletePost(@PathVariable Long id) {
        postService.deletePost(id);
        return ResponseEntity.noContent().build();
    }


    @PostMapping("/{id}/report/")
    public ResponseEntity<Void> reportPost(@PathVariable Long id) {
        // Implement your report logic here
        return ResponseEntity.ok().build();
    }


    @GetMapping("/{id}/image")
    public ResponseEntity<byte[]> getImage(@PathVariable Long id) {
        // 게시물 ID를 기반으로 해당 게시물의 이미지 URL을 가져옴
        Optional<Post> postOptional = postService.getPostById(id);

        // 게시물이 존재하는지 확인
        if (postOptional.isPresent()) {
            Post post = postOptional.get();

            try {
                // 이미지 파일의 경로를 가져옴
                Path imagePath = Paths.get(post.getImageUrl());

                // 이미지 파일을 바이트 배열로 읽어들임
                byte[] imageData = Files.readAllBytes(imagePath);

                // 이미지 데이터를 응답에 실어 반환
                return ResponseEntity.ok().contentType(MediaType.IMAGE_JPEG).body(imageData);
            } catch (IOException e) {
                // 이미지 파일을 읽어오는 도중 오류가 발생한 경우
                e.printStackTrace();
                return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(null);
            }
        } else {
            // 해당 ID에 해당하는 게시물이 없는 경우
            return ResponseEntity.notFound().build();
        }
    }


    /**
     * 썸네일 저장
     */
    @PostMapping("/{boardID}/thumbnail")
    public ResponseEntity<String> saveThumbnail(
            @PathVariable Long boardID,
            @RequestBody Map<String, String> requestBody
    ) {
        if (requestBody == null || !requestBody.containsKey("body")) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body("body 값이 필요합니다.");
        }

        String body = requestBody.get("body");
        postService.saveThumbnail(boardID, body);
        return ResponseEntity.ok("썸네일 저장 완료");
    }


    /**
     * 썸네일 반환
     */
    @GetMapping("/{boardID}/thumbnail")
    public ResponseEntity<String> getThumbnail(@PathVariable Long boardID) {
        String thumbnailUrl = postService.getThumbnail(boardID);
        if (thumbnailUrl != null) {
            return ResponseEntity.ok(thumbnailUrl);
        } else {
            return ResponseEntity.notFound().build();
        }
    }


    /**
     * 검색
     */
    @GetMapping("/search")
    public ResponseEntity<List<Post>> searchPosts(@RequestParam String query) {
        List<Post> posts = postService.searchPosts(query);
        if (posts.isEmpty()) {
            return ResponseEntity.noContent().build();  // 검색 결과가 없는 경우
        }
        return ResponseEntity.ok(posts);  // 검색 결과가 있는 경우
    }


    @GetMapping("/user/{nickname}")
    public ResponseEntity<List<Post>> getPostsByUser(@PathVariable String nickname) {
        List<Post> posts = postService.getPostsByUser(nickname);
        if (!posts.isEmpty()) {
            return ResponseEntity.ok(posts);
        } else {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).build();
        }
    }

    /**
     * title과 boardID
     */
    @GetMapping("/titles/{placeName}")
    public ResponseEntity<List<Map<String, Object>>> getTitleBoardID(
            @PathVariable String placeName) {
        List<Map<String, Object>> titles = postService.getTitleBoardID(placeName);
        return ResponseEntity.ok(titles);
    }




}
