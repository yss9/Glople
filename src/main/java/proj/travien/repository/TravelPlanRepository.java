package proj.travien.repository;

import org.springframework.data.jpa.repository.EntityGraph;
import org.springframework.data.jpa.repository.JpaRepository;
import proj.travien.domain.TravelPlan;

import java.util.List;
import java.util.Optional;

// 장소(places)는 응답을 만들 때 항상 필요하므로 같은 쿼리에서 함께 읽는다(@EntityGraph). 트랜잭션 밖에서 DTO로 바꿔도 지연 로딩이 일어나지 않는다.
public interface TravelPlanRepository extends JpaRepository<TravelPlan, Long> {

    // 같은 사용자가 같은 clientRequestId로 저장한 일정 (멱등 저장)
    @EntityGraph(attributePaths = "places")
    Optional<TravelPlan> findWithPlacesByUser_IdAndClientRequestId(Long userId, String clientRequestId);

    // 주인이 userId인 일정. 없거나 남의 것이면 빈 값 (컨트롤러는 둘 다 403)
    @EntityGraph(attributePaths = "places")
    Optional<TravelPlan> findWithPlacesByIdAndUser_Id(Long id, Long userId);

    // 관리자 조회용 (주인 무관)
    @EntityGraph(attributePaths = "places")
    Optional<TravelPlan> findWithPlacesById(Long id);

    // 내 일정 목록, 최근에 저장한 것부터
    @EntityGraph(attributePaths = "places")
    List<TravelPlan> findWithPlacesByUser_IdOrderByCreatedAtDescIdDesc(Long userId);
}
