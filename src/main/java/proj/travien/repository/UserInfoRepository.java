package proj.travien.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import proj.travien.domain.UserInfo;

import java.util.List;

public interface UserInfoRepository extends JpaRepository<UserInfo, Long> {

    List<UserInfo> findByGenderAndAgeBetween(String gender, String fromBirthDate, String toBirthDate);
}
