package proj.travien.service;

import jakarta.persistence.EntityManager;
import jakarta.persistence.EntityManagerFactory;
import jakarta.persistence.Persistence;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.http.HttpStatus;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;
import proj.travien.domain.Addresses;
import proj.travien.domain.Post;
import proj.travien.domain.User;
import proj.travien.dto.AddressResponseDto;
import proj.travien.repository.AddressesRepository;
import proj.travien.repository.PostRepository;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Collectors;


@Service
public class PostService {

    @Autowired
    private PostRepository postRepository;

    @Autowired
    private AddressesRepository addressesRepository;





    @Transactional
    public void writePost(Post post) { // title, body, address
        postRepository.save(post);
    }

    @Transactional(readOnly = true)
    public Page<Post> postList(Pageable pageable){
        return postRepository.findAll(pageable);
    }


    @Transactional(readOnly = true)
    public Post postDetail(Long boardId) {
        return postRepository.findById(boardId)
                .orElseThrow(()->{
                    return new IllegalArgumentException("글 상세보기 실패 : 아이디를 찾을 수 없습니다.");
                });
    }

    @Transactional
    public void postDelete(Long boardId) {
        System.out.println("글삭제하기 : "+ boardId);
        postRepository.deleteById(boardId);
    }


    @Transactional
    public void postModify(Long boardId, Post requestBoard) {
        Post post = postRepository.findById(boardId)
                .orElseThrow(()->{
                    return new IllegalArgumentException("글 찾기 실패 : 아이디를 찾을 수 없습니다.");
                }); // 영속화 완료
        post.setTitle(requestBoard.getTitle());
        post.setBody(requestBoard.getBody());
        // 해당 함수로 종료시(Service가 종료될 때) 트랜잭션이 종료됩니다. 이때 더티체킹 - 자동 업데이트가 됨. db flush
    }



  /*  @Transactional
    public void 댓글쓰기(ReplySaveRequestDto replySaveRequestDto) {
        int result = replyRepository.mSave(replySaveRequestDto.getUserId(), replySaveRequestDto.getBoardId(), replySaveRequestDto.getContent());
        System.out.println("BoardService : "+result);
    }

    @Transactional
    public void 댓글삭제(int replyId) {
        replyRepository.deleteById(replyId);
    }*/








    private static final String UPLOAD_DIR = "src/main/resources/static/image/";


    public List<Post> getAllPosts() {
        return postRepository.findAll();
    }



    /**
     * 게시물 업로드
     */
    public Post createPost(String title, String body, Set<Addresses> addressStrings,
                           String addressTitle, String country) {
        Post post = new Post();
        post.setTitle(title);
        post.setBody(body);
        post.setAddressTitle(addressTitle);
        post.setCountry(country);

        Set<Addresses> addresses = getAddressesSet(addressStrings, post);

        post.setAddresses(addresses);

        return postRepository.save(post);
    }

    private Set<Addresses> getAddressesSet(Set<Addresses> addressStrings, Post post) {
        Set<Addresses> addresses = new HashSet<>();
        for (Addresses address : addressStrings) {
            address.setPost(post);
            addresses.add(address);
        }
        return addresses;
    }


    /**
     * 게시물 작성자(Post.nickname = 본인 DB 닉네임) 또는 관리자인지 확인한다. 수정·삭제·썸네일 저장 전에 부른다.
     * 게시물이 없으면 404, 작성자가 아니면 403.
     * (getPostById는 imageUrl을 바꿔 돌려주므로 여기서는 저장소를 직접 읽는다)
     */
    public Post requireEditablePost(Long boardID, User me, boolean admin) {
        Post post = postRepository.findById(boardID)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "게시물을 찾을 수 없습니다."));
        boolean owner = post.getNickname() != null && post.getNickname().equals(me.getNickname());
        if (!owner && !admin) {
            throw new AccessDeniedException("게시물 작성자만 할 수 있습니다.");
        }
        return post;
    }

    /**
     * 게시물 수정
     */
    @Transactional
    public Post updatePost(Long boardID, Post updatedPost, Set<Addresses> addresses) {
        Post post = postRepository.findById(boardID)
                .orElseThrow(() -> new RuntimeException("게시물을 찾을 수 없습니다."));

        // 기존 주소 삭제
        addressesRepository.deleteAll(post.getAddresses());

        // 게시물의 제목, 본문, 나라, 주소 제목 등을 업데이트
        post.setTitle(updatedPost.getTitle());
        post.setCountry(updatedPost.getCountry());
        post.setBody(updatedPost.getBody());
        post.setAddressTitle(updatedPost.getAddressTitle());

        // 새 주소들을 추가
        Set<Addresses> updatedAddresses = getAddressesSet(addresses, post);
        post.setAddresses(updatedAddresses);

        // 썸네일 업데이트
        String newThumbnail = extractFirstImageUrl(updatedPost.getBody());
        if (newThumbnail == null) {
            newThumbnail = "/image/no_image.png"; // 기본 이미지
        }
        post.setThumbnail(newThumbnail);

        return postRepository.save(post);
    }





    /**
     * 게시물 가져오기
     */
    public Optional<Post> getPostById(Long id) {
        Optional<Post> post = postRepository.findById(id);
        if (post.isPresent()) {
            Post fetchedPost = post.get();
            fetchedPost.setImageUrl("http://localhost:8000/api/posts/" + fetchedPost.getBoardID() + "/image");
            return Optional.of(fetchedPost);
        }
        return Optional.empty();
    }



    /**
     * 루트 추천 (특정 placename에 맞는 포스트 검색)
     */
    private final ObjectMapper objectMapper;

    public PostService(AddressesRepository addressesRepository, PostRepository postRepository, ObjectMapper objectMapper) {
        this.postRepository = postRepository;
        this.objectMapper = objectMapper;
        this.addressesRepository = addressesRepository;
    }


    @Transactional(readOnly = true)
    public List<AddressResponseDto> getPostAddressesByPlaceName(String placename) {
        // 검색을 위해 placename을 정규화 (공백 및 특수문자 제거)
        String normalizedPlacename = normalizeString(placename);

        List<Post> posts = postRepository.findAll();

        // placename과 일치하는 주소 또는 제목이 있는 모든 포스트를 찾음
        List<AddressResponseDto> matchingPosts = posts.stream()
                .filter(post -> {
                    // 제목과 주소를 정규화 (공백 및 특수문자 제거)
                    String normalizedTitle = normalizeString(post.getAddressTitle());

                    // placename이 제목에 포함되어 있는지 확인
                    boolean titleMatches = normalizedTitle.contains(normalizedPlacename);

                    // placename이 주소에 포함되어 있는지 확인
                    boolean addressMatches = post.getAddresses().stream()
                            .anyMatch(address -> {
                                String rawAddress = address.getAddress();

                                try {
                                    // 주소 문자열을 정리하여 JSON 배열로 변환
                                    String sanitizedAddress = sanitizeAddress(rawAddress);

                                    // JSON 배열 파싱
                                    List<String> addressList = objectMapper.readValue(sanitizedAddress, new TypeReference<List<String>>() {});

                                    // 각 주소 항목을 정규화하여 비교
                                    return addressList.stream()
                                            .map(this::normalizeString)
                                            .anyMatch(normalizedAddress -> normalizedAddress.contains(normalizedPlacename));
                                } catch (Exception e) {
                                    e.printStackTrace();
                                    return false;
                                }
                            });

                    return titleMatches || addressMatches;
                })
                .map(post -> new AddressResponseDto(post.getAddressTitle(), post.getAddresses(), post.getBoardID()))
                .collect(Collectors.toList());

        if (!matchingPosts.isEmpty()) {
            return matchingPosts;
        } else {
            throw new IllegalStateException("추천되는 게시물 없음");
        }
    }


    /**
     * 주소 문자열을 JSON 배열 형식으로 정리
     */
    private String sanitizeAddress(String rawAddress) {
        // 불완전한 JSON 배열 형식을 수정
        String sanitizedAddress = rawAddress;

        if (!sanitizedAddress.startsWith("[")) {
            sanitizedAddress = "[" + sanitizedAddress;
        }
        if (!sanitizedAddress.endsWith("]")) {
            sanitizedAddress = sanitizedAddress + "]";
        }

        // 잘못된 구문 제거 및 문자열 정리
        sanitizedAddress = sanitizedAddress.replaceAll("\\\\", "").replaceAll("\"\"", "\"");

        return sanitizedAddress;
    }

    /**
     * 문자열 정규화 (공백 및 특수문자 제거, 소문자 변환)
     */
    private String normalizeString(String input) {
        return input.replaceAll("\\s+", "").replaceAll("[^\\p{IsAlphabetic}\\p{IsDigit}]", "").toLowerCase();
    }


    /**
     * body에서 썸네일을 추출하는 메서드
     */

    public void saveThumbnail(Long boardID, String body) {
        Post post = postRepository.findById(boardID)
                .orElseThrow(() -> new RuntimeException("게시물을 찾을 수 없습니다."));

        // 본문(body) 내 첫 번째 <img> 태그의 src 속성 추출
        String thumbnailUrl = extractFirstImageUrl(body);

        if (thumbnailUrl == null) {
            // 이미지가 없을 경우 기본 이미지 경로 설정
            thumbnailUrl = "/image/no_image.png";
        }

        post.setThumbnail(thumbnailUrl);
        postRepository.save(post);
    }

    private String extractFirstImageUrl(String body) {
        // <img> 태그의 src 속성 추출 정규표현식
        Pattern pattern = Pattern.compile("<img[^>]+src=\"([^\"]+)\"");
        Matcher matcher = pattern.matcher(body);

        if (matcher.find()) {
            return matcher.group(1); // 첫 번째 이미지 URL 반환
        }
        return null; // 이미지가 없을 경우 null 반환
    }



    /**
     * 썸네일 반환
     */
    @Transactional(readOnly = true)
    public String getThumbnail(Long boardID) {
        // 게시물을 조회하여 썸네일을 가져옴
        Post post = postRepository.findById(boardID)
                .orElseThrow(() -> new IllegalArgumentException("게시물을 찾을 수 없습니다: " + boardID));

        // 썸네일이 존재하면 반환, 그렇지 않으면 null 반환
        return post.getThumbnail();
    }

    /**
     * 나라별 게시물 가져오기
     */
    @Transactional(readOnly = true)
    public List<Post> getPostsByCountry(String country) {
        return postRepository.findByCountry(country);
    }



    public void deletePost(Long id) {
        postRepository.deleteById(id);
    }

    /**
     * 검색44
     */
    @Transactional(readOnly = true)
    public List<Post> searchPosts(String keyword) {
        if (keyword == null || keyword.trim().isEmpty()) {
            return Collections.emptyList();  // 빈 문자열일 경우 빈 리스트 반환
        }
        return postRepository.findByKeyword(keyword);
    }

    @Transactional(readOnly = true)
    public List<Post> getPostsByUser(String nickname) {
        return postRepository.findByNickname(nickname);
    }


    /**
     * title과 boardID만 존재하는 목록
     */
    // Service Layer
    @Transactional(readOnly = true)
    public List<Map<String, Object>> getTitleBoardID(String placeName) {
        return postRepository.findByTitleContaining(placeName).stream()
                .map(post -> {
                    Map<String, Object> map = new HashMap<>();
                    map.put("title", post.getTitle());
                    map.put("boardID", post.getBoardID());
                    map.put("profilePicture", post.getProfilePicture());  // profilePicture 추가
                    return map;
                })
                .collect(Collectors.toList());
    }




}
