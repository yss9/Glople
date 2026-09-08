package proj.travien.service;

import org.junit.jupiter.api.Test;
import proj.travien.domain.UserInfo;
import proj.travien.repository.UserInfoRepository;

import java.util.List;
import java.util.stream.LongStream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class UserInfoRecommendationServiceTest {

    private final UserInfoRepository repository = mock(UserInfoRepository.class);
    private final UserInfoRecommendationService service = new UserInfoRecommendationService(repository);

    @Test
    void narrowsCandidatesAndKeepsOnlyTopTwelveScores() {
        UserInfo target = user(null, "19900101", "M", "E80/N70/T60/J50");
        List<UserInfo> candidates = LongStream.rangeClosed(1, 20)
                .mapToObj(id -> user(id, "19900101", "M", "ENTJ"))
                .toList();
        when(repository.findByGenderAndAgeBetween("M", "19850101", "19950101"))
                .thenReturn(candidates);

        List<UserInfo> result = service.getSimilarUsers(target);

        assertThat(result).hasSize(12);
        assertThat(result.get(0).getId()).isEqualTo(20L);
        verify(repository).findByGenderAndAgeBetween("M", "19850101", "19950101");
    }

    private UserInfo user(Long id, String age, String gender, String mbti) {
        UserInfo user = new UserInfo();
        user.setId(id);
        user.setAge(age);
        user.setGender(gender);
        user.setMbti(mbti);
        return user;
    }
}
