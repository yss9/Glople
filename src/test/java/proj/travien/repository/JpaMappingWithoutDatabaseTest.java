package proj.travien.repository;

import jakarta.persistence.Entity;
import jakarta.persistence.EntityManager;
import jakarta.persistence.OrderBy;
import org.hibernate.SessionFactory;
import org.hibernate.boot.Metadata;
import org.hibernate.boot.MetadataSources;
import org.hibernate.boot.model.naming.CamelCaseToUnderscoresNamingStrategy;
import org.hibernate.boot.registry.StandardServiceRegistry;
import org.hibernate.boot.registry.StandardServiceRegistryBuilder;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.config.BeanDefinition;
import org.springframework.boot.orm.jpa.hibernate.SpringImplicitNamingStrategy;
import org.springframework.context.annotation.ClassPathScanningCandidateComponentProvider;
import org.springframework.core.type.filter.AnnotationTypeFilter;
import org.springframework.data.jpa.repository.support.JpaRepositoryFactory;

import java.lang.reflect.Field;
import java.lang.reflect.ParameterizedType;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * DB 없이 Hibernate를 MySQL 방언으로 띄워 엔티티 매핑, 스프링 데이터 저장소의 파생 쿼리(메서드 이름)·JPQL을 검사하고
 * ddl-auto=update가 만들 표를 DDL 문자열로 확인한다. (JDBC 메타데이터를 읽지 않게 해서 연결을 열지 않는다)
 * 실제 MySQL에서 표가 만들어지는지와 native query 실행은 오너가 서버를 띄워 확인한다.
 */
class JpaMappingWithoutDatabaseTest {

    private static StandardServiceRegistry registry;
    private static SessionFactory sessionFactory;
    private static Path ddlFile;

    @BeforeAll
    static void boot() throws Exception {
        // SessionFactory를 만들 때 create 스크립트만 파일로 쓴다 (DB에는 아무것도 하지 않음)
        ddlFile = Files.createTempFile("glople-ddl", ".sql");
        registry = new StandardServiceRegistryBuilder()
                .applySetting("jakarta.persistence.schema-generation.database.action", "none")
                .applySetting("jakarta.persistence.schema-generation.scripts.action", "create")
                .applySetting("jakarta.persistence.schema-generation.scripts.create-target", ddlFile.toString())
                .applySetting("hibernate.hbm2ddl.delimiter", ";")
                .applySetting("hibernate.dialect", "org.hibernate.dialect.MySQLDialect")
                .applySetting("jakarta.persistence.database-product-name", "MySQL")
                .applySetting("jakarta.persistence.database-major-version", "8")
                .applySetting("jakarta.persistence.database-minor-version", "0")
                .applySetting("hibernate.boot.allow_jdbc_metadata_access", "false")
                .applySetting("hibernate.temp.use_jdbc_metadata_defaults", "false")
                // 스프링 부트 기본 이름 규칙 (camelCase → snake_case)
                .applySetting("hibernate.physical_naming_strategy", CamelCaseToUnderscoresNamingStrategy.class.getName())
                .applySetting("hibernate.implicit_naming_strategy", SpringImplicitNamingStrategy.class.getName())
                .build();
        MetadataSources sources = new MetadataSources(registry);
        for (BeanDefinition candidate : entityCandidates()) {
            sources.addAnnotatedClassName(candidate.getBeanClassName());
        }
        Metadata metadata = sources.buildMetadata();
        sessionFactory = metadata.buildSessionFactory();
    }

    @AfterAll
    static void close() throws Exception {
        if (sessionFactory != null) {
            sessionFactory.close();
        }
        StandardServiceRegistryBuilder.destroy(registry);
        Files.deleteIfExists(ddlFile);
    }

    // 저장소를 만들 때 스프링 데이터가 메서드 이름(속성 경로)과 JPQL을 검사한다. 틀리면 서버 기동이 실패하는 종류의 오류
    @Test
    void repositoriesCreateTheirQueries() {
        try (EntityManager entityManager = sessionFactory.createEntityManager()) {
            JpaRepositoryFactory factory = new JpaRepositoryFactory(entityManager);
            assertThat(factory.getRepository(TravelPlanRepository.class)).isNotNull();
            assertThat(factory.getRepository(DestinationInfoRepository.class)).isNotNull();
            assertThat(factory.getRepository(UserRepository.class)).isNotNull();
        }
    }

    // @OrderBy 글은 Hibernate가 처음 읽을 때 해석해서 기동 때 잡히지 않는다. 적힌 속성이 대상 엔티티에 있는지 직접 본다.
    @Test
    void orderByClausesNameExistingProperties() throws Exception {
        int checked = 0;
        for (BeanDefinition candidate : entityCandidates()) {
            Class<?> entity = Class.forName(candidate.getBeanClassName());
            for (Field field : entity.getDeclaredFields()) {
                OrderBy orderBy = field.getAnnotation(OrderBy.class);
                if (orderBy == null) {
                    continue;
                }
                Class<?> element = (Class<?>) ((ParameterizedType) field.getGenericType()).getActualTypeArguments()[0];
                for (String part : orderBy.value().split(",")) {
                    String property = part.trim().split("\\s+")[0];
                    assertThat(hasField(element, property)).as(entity.getSimpleName() + "." + field.getName() + " → " + property).isTrue();
                    checked++;
                }
            }
        }
        assertThat(checked).isGreaterThanOrEqualTo(2); // TravelPlan.places: position, id
    }

    @Test
    void travelPlanTablesHaveTheUniqueKeyAndForeignKeys() throws Exception {
        String ddl = ddl();

        assertThat(ddl).contains("create table travel_plan (");
        assertThat(ddl).contains("create table travel_plan_place (");
        assertThat(table(ddl, "travel_plan"))
                .contains("client_request_id varchar(80) not null")
                .contains("title varchar(100) not null")
                .contains("region varchar(50) not null")
                .contains("days integer not null")
                .contains("created_at datetime(6) not null")
                .contains("user_id bigint not null");
        assertThat(table(ddl, "travel_plan_place"))
                .contains("position_no integer not null")
                .contains("day_no integer not null")
                .contains("name varchar(50) not null")
                .contains("plan_id bigint not null")
                // ENUM 열이 아니라 VARCHAR (ddl-auto=update는 ENUM 값 목록을 고치지 않는다)
                .contains("source varchar(20) not null");
        assertThat(ddl).contains("alter table travel_plan add constraint uk_travel_plan_user_client_request unique (user_id, client_request_id)");
        assertThat(ddl).containsPattern("alter table travel_plan add constraint \\w+ foreign key \\(user_id\\) references user \\(id\\)");
        assertThat(ddl).containsPattern("alter table travel_plan_place add constraint \\w+ foreign key \\(plan_id\\) references travel_plan \\(id\\)");
        assertThat(ddl).contains("create index idx_travel_plan_place_plan on travel_plan_place (plan_id)");
    }

    // 태거 예측 열과 규칙 판 열 (ddl-auto=update가 기존 표에 ADD COLUMN). 마스크 열은 NOT NULL DEFAULT 0이라 기존 행은 0으로 채워진다
    @Test
    void destinationInfoHasTaggerAndRuleVersionColumns() throws Exception {
        assertThat(table(ddl(), "destination_info"))
                .contains("keyword_mask_low BIGINT DEFAULT 0 not null")
                .contains("keyword_mask_high BIGINT DEFAULT 0 not null")
                .contains("keyword_mask_version varchar(40)")
                .contains("tagger_mask_low BIGINT DEFAULT 0 not null")
                .contains("tagger_mask_high BIGINT DEFAULT 0 not null")
                .contains("tagger_model varchar(100)")
                .contains("tagger_info_crc32 bigint")
                .contains("tagger_updated_at datetime(6)")
                .doesNotContain("tagger_model varchar(100) not null");
        assertThat(ddl()).contains("create index idx_destination_source_external_id on destination_info (source, external_id)");
    }

    private static String ddl() throws Exception {
        return Files.readString(ddlFile).replaceAll("\\s+", " ");
    }

    private static Set<BeanDefinition> entityCandidates() {
        ClassPathScanningCandidateComponentProvider scanner = new ClassPathScanningCandidateComponentProvider(false);
        scanner.addIncludeFilter(new AnnotationTypeFilter(Entity.class));
        return scanner.findCandidateComponents("proj.travien.domain");
    }

    private static boolean hasField(Class<?> type, String name) {
        for (Class<?> c = type; c != null; c = c.getSuperclass()) {
            for (Field field : c.getDeclaredFields()) {
                if (field.getName().equals(name)) {
                    return true;
                }
            }
        }
        return false;
    }

    // "create table 이름 ( ... );" 한 문장
    private static String table(String ddl, String name) {
        int start = ddl.indexOf("create table " + name + " (");
        return ddl.substring(start, ddl.indexOf(";", start));
    }
}
