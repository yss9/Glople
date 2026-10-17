package proj.travien.dto;

import java.util.List;

public record TourApiPage(int totalCount, List<TourApiItem> items) {
}
