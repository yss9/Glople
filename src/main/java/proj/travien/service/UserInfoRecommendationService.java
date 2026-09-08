package proj.travien.service;

import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import proj.travien.domain.UserInfo;
import proj.travien.repository.UserInfoRepository;

import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.PriorityQueue;

@Service
@RequiredArgsConstructor
public class UserInfoRecommendationService {

    private static final int TOP_K = 12;
    private static final int BIRTH_YEAR_WINDOW = 5;
    private static final DateTimeFormatter BASIC_DATE = DateTimeFormatter.BASIC_ISO_DATE;
    private static final DateTimeFormatter ISO_DATE = DateTimeFormatter.ISO_LOCAL_DATE;

    private static final double MAX_AGE = 100.0;

    private final UserInfoRepository userInfoRepository;

    public List<UserInfo> getSimilarUsers(UserInfo newUser) {
        validateRecommendationInput(newUser);

        DateRange candidateRange = candidateBirthDateRange(newUser.getAge());
        List<UserInfo> candidates = userInfoRepository.findByGenderAndAgeBetween(
                newUser.getGender(),
                candidateRange.from(),
                candidateRange.to()
        );

        UserInfo normalizedUser = copyWithNormalizedMbti(newUser);
        double[] newUserVector = convertToVector(normalizedUser);
        Comparator<UserSimilarity> similarityComparator = Comparator
                .comparingDouble(UserSimilarity::similarity)
                .thenComparingLong(item -> item.user().getId() == null ? Long.MIN_VALUE : item.user().getId());
        PriorityQueue<UserSimilarity> topScores = new PriorityQueue<>(TOP_K, similarityComparator);

        for (UserInfo candidate : candidates) {
            if (newUser.getId() != null && newUser.getId().equals(candidate.getId())) {
                continue;
            }

            UserInfo normalizedCandidate = copyWithNormalizedMbti(candidate);
            UserSimilarity scored = new UserSimilarity(
                    candidate,
                    cosineSimilarity(newUserVector, convertToVector(normalizedCandidate))
            );

            if (topScores.size() < TOP_K) {
                topScores.offer(scored);
            } else if (similarityComparator.compare(scored, topScores.peek()) > 0) {
                topScores.poll();
                topScores.offer(scored);
            }
        }

        return topScores.stream()
                .sorted(similarityComparator.reversed())
                .map(UserSimilarity::user)
                .toList();
    }

    private void validateRecommendationInput(UserInfo user) {
        if (user == null || user.getAge() == null || user.getGender() == null || user.getMbti() == null) {
            throw new IllegalArgumentException("age, gender and mbti are required");
        }
    }

    private double[] convertToVector(UserInfo user) {
        double[] vector = new double[6];

        vector[0] = calculateAge(user.getAge()) / MAX_AGE;
        vector[1] = user.getGender().equalsIgnoreCase("M") ? 0 : 1;
        vector[2] = user.getMbti().charAt(0) == 'E' ? 0 : 1;
        vector[3] = user.getMbti().charAt(1) == 'S' ? 0 : 1;
        vector[4] = user.getMbti().charAt(2) == 'T' ? 0 : 1;
        vector[5] = user.getMbti().charAt(3) == 'J' ? 0 : 1;

        return vector;
    }

    private int calculateAge(String birthDateString) {
        return java.time.Period.between(parseBirthDate(birthDateString), LocalDate.now()).getYears();
    }

    private double cosineSimilarity(double[] vec1, double[] vec2) {
        double dotProduct = 0.0;
        double magnitude1 = 0.0;
        double magnitude2 = 0.0;

        for (int i = 0; i < vec1.length; i++) {
            dotProduct += vec1[i] * vec2[i];
            magnitude1 += Math.pow(vec1[i], 2);
            magnitude2 += Math.pow(vec2[i], 2);
        }

        magnitude1 = Math.sqrt(magnitude1);
        magnitude2 = Math.sqrt(magnitude2);

        if (magnitude1 == 0 || magnitude2 == 0) {
            return 0;
        }

        return dotProduct / (magnitude1 * magnitude2);
    }

    private DateRange candidateBirthDateRange(String birthDateValue) {
        LocalDate birthDate = parseBirthDate(birthDateValue);
        DateTimeFormatter formatter = birthDateValue.contains("-") ? ISO_DATE : BASIC_DATE;
        return new DateRange(
                birthDate.minusYears(BIRTH_YEAR_WINDOW).format(formatter),
                birthDate.plusYears(BIRTH_YEAR_WINDOW).format(formatter)
        );
    }

    private LocalDate parseBirthDate(String value) {
        try {
            return LocalDate.parse(value, value.contains("-") ? ISO_DATE : BASIC_DATE);
        } catch (DateTimeParseException exception) {
            throw new IllegalArgumentException("Birth date must use yyyyMMdd or yyyy-MM-dd format", exception);
        }
    }

    private UserInfo copyWithNormalizedMbti(UserInfo source) {
        UserInfo copy = new UserInfo();
        copy.setId(source.getId());
        copy.setAge(source.getAge());
        copy.setGender(source.getGender());
        copy.setMbti(normalizeMbti(source.getMbti()));
        copy.setTravelDestinations(source.getTravelDestinations());
        return copy;
    }

    private String normalizeMbti(String value) {
        String normalized = value.trim().toUpperCase(Locale.ROOT);
        if (normalized.contains("/")) {
            normalized = List.of(normalized.split("/")).stream()
                    .filter(part -> !part.isBlank())
                    .map(part -> part.substring(0, 1))
                    .reduce("", String::concat);
        }

        if (normalized.length() < 4) {
            throw new IllegalArgumentException("MBTI must contain four dimensions");
        }

        return normalized.substring(0, 4);
    }

    private record UserSimilarity(UserInfo user, double similarity) {
    }

    private record DateRange(String from, String to) {
    }
}
