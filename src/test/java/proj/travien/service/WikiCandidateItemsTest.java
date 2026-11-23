package proj.travien.service;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class WikiCandidateItemsTest {

    @Test
    void loadsTheCandidateList() {
        List<String> qids = WikiCandidateItems.qids();

        // 2026-10-17 집계: 유형 코드표 14,312곳 + 유형 없이 세계유산으로만 들어온 106곳
        assertThat(qids).hasSize(14_418).doesNotHaveDuplicates().allMatch(qid -> qid.matches("Q[1-9][0-9]*"));
        // 에펠탑, 후지산, 루브르 박물관, 기요미즈데라
        assertThat(qids).contains("Q243", "Q39231", "Q19675", "Q221716");
        // 대한민국·북한이 국가에 들어 있거나 소재지가 한반도인 항목은 넣지 않았다.
        // (독도 Q20317, 독도 천연보호구역 Q21709690, 경성신사 Q10855232, 백두산 Q107635)
        assertThat(qids).doesNotContain("Q20317", "Q21709690", "Q10855232", "Q107635");
        // 한 번에 확인하는 묶음 크기 안에서 나눠 보낼 수 있다.
        assertThat((qids.size() + 1_999) / 2_000).isEqualTo(8);
    }

    @Test
    void skipsCommentsAndFailsFastOnMalformedLines() {
        assertThat(WikiCandidateItems.parse(List.of("﻿# 주석", "", "Q1", " Q20 ", "# 중간 주석", "Q300")))
                .containsExactly("Q1", "Q20", "Q300");

        assertThatThrownBy(() -> WikiCandidateItems.parse(List.of("# 주석만")))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("하나도 없습니다");
        assertThatThrownBy(() -> WikiCandidateItems.parse(List.of("Q1", "q2")))
                .isInstanceOf(IllegalStateException.class).hasMessageContainingAll("2번째 줄", "QID 형식");
        assertThatThrownBy(() -> WikiCandidateItems.parse(List.of("Q1", "Q1")))
                .isInstanceOf(IllegalStateException.class).hasMessageContainingAll("2번째 줄", "중복");
    }
}
