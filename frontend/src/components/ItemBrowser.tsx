import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, formatWon, type Item } from "../api";

/**
 * 매물 목록 — 질문하지 않아도 무엇을 파는지 보이는 화면.
 *
 * 이게 없던 동안 랜딩은 **빈 검색창 하나**였다. 거래소인데 매물을 볼 수가 없고,
 * 링크를 처음 여는 사람은 뭘 물어봐야 하는지조차 모른다.
 *
 * ## 열이 이것뿐인 이유
 *
 * 종류·속성·강화·요구레벨은 **Postgres `items` 에 없다.** ES 매핑에만 있고,
 * `export_demo_sql.py` 가 "화면을 위해 백엔드 스키마를 늘리지 않는다 — 거래 상태는
 * Postgres 가, 검색용 속성은 ES 가 갖는 분담을 유지한다"고 명시적으로 정해뒀다.
 * 그 결정을 화면 사정으로 뒤집지 않는다. 속성까지 보이게 하는 건 별도 결정이고
 * ADR 이 필요하다(상세 화면도 같은 이유로 속성을 못 보여주고 있다).
 *
 * ## TanStack Table 을 쓰지 않았다
 *
 * 정렬·페이징을 **서버가 한다.** 그러면 그 라이브러리가 파는 것(클라이언트 정렬·
 * 필터·가상화)이 전부 남고, 열 6개짜리 표에 헤드리스 추상화만 얹힌다. 필요해지는
 * 시점은 클라이언트 쪽 상호작용이 생길 때다.
 */
const PAGE_SIZE = 20;

type SortKey = "price" | "createdAt" | "name";

export default function ItemBrowser() {
  const navigate = useNavigate();
  const [page, setPage] = useState(0);
  const [sortKey, setSortKey] = useState<SortKey>("createdAt");
  const [desc, setDesc] = useState(true);

  const listing = useQuery({
    queryKey: ["items", page, sortKey, desc],
    queryFn: () =>
      api.items({
        page,
        size: PAGE_SIZE,
        sort: `${sortKey},${desc ? "desc" : "asc"}`,
      }),
    // 페이지를 넘길 때 표가 통째로 사라졌다 다시 그려지면 눈이 튄다.
    // 새 페이지가 올 때까지 이전 페이지를 남겨둔다.
    placeholderData: keepPreviousData,
  });

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setDesc((value) => !value);
    } else {
      setSortKey(key);
      // 가격·등록일은 큰 값부터, 이름은 가나다순이 기본이다.
      setDesc(key !== "name");
    }
    setPage(0);
  }

  if (listing.isPending) return <p className="muted">매물을 불러오는 중…</p>;
  if (listing.isError)
    return <p className="error">{(listing.error as Error).message}</p>;

  const { content, totalElements, totalPages, number } = listing.data;

  return (
    <div className="stack">
      <div className="row">
        <strong>전체 매물</strong>
        <span className="badge">
          <strong>{totalElements.toLocaleString("ko-KR")}건</strong>
        </span>
        {listing.isFetching && <span className="muted">갱신 중…</span>}
      </div>

      <div className="card" style={{ overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <SortableHeader
                label="이름"
                active={sortKey === "name"}
                desc={desc}
                onClick={() => toggleSort("name")}
              />
              <th>판매자</th>
              <th>형태</th>
              <th style={{ textAlign: "right" }}>재고</th>
              <SortableHeader
                label="가격"
                align="right"
                active={sortKey === "price"}
                desc={desc}
                onClick={() => toggleSort("price")}
              />
              <SortableHeader
                label="등록"
                active={sortKey === "createdAt"}
                desc={desc}
                onClick={() => toggleSort("createdAt")}
              />
            </tr>
          </thead>
          <tbody>
            {content.map((item) => (
              <ItemRow
                key={item.id}
                item={item}
                onOpen={() => navigate(`/items/${item.id}`)}
              />
            ))}
          </tbody>
        </table>
      </div>

      <div className="row">
        <button
          type="button"
          disabled={number === 0}
          onClick={() => setPage((p) => p - 1)}
        >
          ← 이전
        </button>
        <span className="muted">
          {number + 1} / {Math.max(totalPages, 1)}
        </span>
        <button
          type="button"
          disabled={number + 1 >= totalPages}
          onClick={() => setPage((p) => p + 1)}
        >
          다음 →
        </button>
      </div>
    </div>
  );
}

function SortableHeader({
  label,
  active,
  desc,
  align,
  onClick,
}: {
  label: string;
  active: boolean;
  desc: boolean;
  align?: "right";
  onClick: () => void;
}) {
  return (
    <th style={{ textAlign: align }}>
      <button
        type="button"
        className="linklike"
        onClick={onClick}
        // 정렬 상태를 색이 아니라 **기호**로도 낸다 — 색만 쓰면 어느 열로
        // 정렬 중인지 흑백 화면이나 색각 이상에서 사라진다.
        aria-sort={active ? (desc ? "descending" : "ascending") : "none"}
      >
        {label}
        {active && (desc ? " ↓" : " ↑")}
      </button>
    </th>
  );
}

function ItemRow({ item, onOpen }: { item: Item; onOpen: () => void }) {
  const auction = item.saleType === "AUCTION";
  const soldOut = item.stock < 1 || item.status !== "ON_SALE";
  return (
    <tr
      onClick={onOpen}
      style={{ cursor: "pointer", opacity: soldOut ? 0.55 : undefined }}
    >
      <td>{item.name}</td>
      <td className="muted">{item.sellerUsername}</td>
      <td>
        <span className={`badge ${auction ? "warn" : ""}`}>
          {auction ? "경매" : "고정가"}
        </span>
      </td>
      <td style={{ textAlign: "right" }} className="muted">
        {item.stock}
      </td>
      <td style={{ textAlign: "right" }}>
        {formatWon(auction ? (item.currentBidPrice ?? item.price) : item.price)}
        {auction && <span className="muted"> 입찰가</span>}
      </td>
      <td className="muted">{item.createdAt.slice(0, 10)}</td>
    </tr>
  );
}
