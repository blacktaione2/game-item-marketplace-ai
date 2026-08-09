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

export default function ItemBrowser({ heading }: { heading?: string }) {
  const navigate = useNavigate();
  const [page, setPage] = useState(0);
  const [sortKey, setSortKey] = useState<SortKey>("createdAt");
  const [desc, setDesc] = useState(true);

  /**
   * 요약용 전량 조회. **표와 별개 쿼리다.**
   *
   * 표는 서버 페이징을 그대로 쓰는데(그게 이 화면의 결정이다), 요약은 42건
   * 전체를 봐야 성립한다 — 현재 페이지 20건으로 "최저가"를 내면 **페이지를
   * 넘길 때마다 값이 바뀌는 거짓말**이 된다. 그래서 목적이 다른 두 쿼리를 둔다.
   *
   * 대가는 요청 하나이고, 42행이라 무시할 만하다. 규모가 커지면 이건
   * **집계 엔드포인트로 가야 한다** — 그때는 전량을 받는 것 자체가 틀린 설계다.
   */
  const summary = useQuery({
    queryKey: ["items", "summary"],
    queryFn: () => api.items({ page: 0, size: 200 }),
    staleTime: 60_000,
  });

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

  // **빈 문장 대신 표 모양을 그린다.** 첫 진입에서 이 자리가 한 줄짜리
  // "불러오는 중…" 이면 화면이 통째로 접혔다 펴진다 — 뒤이어 오는 표가
  // 그만큼 밀려 내려와 눈이 튄다.
  if (listing.isPending) return <BrowserSkeleton heading={heading} />;
  if (listing.isError)
    return <p className="error">{(listing.error as Error).message}</p>;

  const { content, totalElements, totalPages, number } = listing.data;

  return (
    <div className="stack">
      <div className="row">
        <strong>{heading ?? "전체 매물"}</strong>
        <span className="badge">
          <strong>{totalElements.toLocaleString("ko-KR")}건</strong>
        </span>
        {listing.isFetching && <span className="muted">갱신 중…</span>}
      </div>

      <Summary items={summary.data?.content} />

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

/**
 * 요약 타일. **표 위의 빈 줄을 채우면서 동시에 정보를 준다.**
 *
 * 값이 아직 없으면 **타일 자리를 비워둔 채 유지한다** — 통째로 감추면 데이터가
 * 도착할 때 아래 표가 밀려 내려간다(레이아웃 시프트).
 *
 * 판매/경매는 `saleType`, 가격은 경매면 현재 입찰가를 쓴다 — 표의 가격 열과
 * 같은 규칙이어야 두 곳이 다른 말을 하지 않는다.
 */
function Summary({ items }: { items?: Item[] }) {
  const stats = (() => {
    if (!items?.length) return null;
    const priceOf = (i: Item) =>
      i.saleType === "AUCTION" ? (i.currentBidPrice ?? i.price) : i.price;
    const prices = items.map(priceOf);
    return {
      auction: items.filter((i) => i.saleType === "AUCTION").length,
      fixed: items.filter((i) => i.saleType !== "AUCTION").length,
      min: Math.min(...prices),
      max: Math.max(...prices),
    };
  })();

  return (
    <div className="summary">
      <Tile label="판매" value={stats ? `${stats.fixed}건` : "—"} />
      <Tile label="경매" value={stats ? `${stats.auction}건` : "—"} />
      <Tile label="최저가" value={stats ? formatWon(stats.min) : "—"} />
      <Tile label="최고가" value={stats ? formatWon(stats.max) : "—"} />
    </div>
  );
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="tile">
      <span className="muted">{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

/** 로딩 중 표 모양. 높이를 미리 차지해 레이아웃 시프트를 막는 것이 목적이다. */
function BrowserSkeleton({ heading }: { heading?: string }) {
  return (
    <div className="stack" aria-busy="true">
      <div className="row">
        <strong>{heading ?? "전체 매물"}</strong>
        <span className="muted">불러오는 중…</span>
      </div>
      <div className="summary">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="tile">
            <span className="skeleton" style={{ width: 48 }} />
            <span className="skeleton" style={{ width: 84, height: 18 }} />
          </div>
        ))}
      </div>
      <div className="card stack">
        {Array.from({ length: 6 }, (_, i) => (
          <span key={i} className="skeleton" style={{ width: `${92 - i * 4}%` }} />
        ))}
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
    // 검색 카드와 같은 이유로 키보드에서도 열려야 한다 — 42행 전부가 Tab 순서
    // 밖에 있었다. `<tr>` 에 `role="button"` 을 주면 표의 의미가 깨지므로,
    // 초점은 행에 두고 역할은 `<td>` 안의 이름이 갖는 편이 낫지만 —
    // 여기서는 행 전체가 클릭 대상이라는 기존 동작을 유지하는 쪽을 골랐다.
    <tr
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
      style={{ cursor: "pointer", opacity: soldOut ? 0.55 : undefined }}
    >
      <td>{item.name}</td>
      <td className="muted">{item.sellerUsername}</td>
      <td>
        {/* 「고정가」가 아니라 「판매」다. 국내 아이템 거래소들이 쓰는 대비이기도
            하고, 「고정가/경매」는 가격 방식을 말하는데 사용자가 고르는 건
            거래 방식이다. */}
        <span className={`badge ${auction ? "warn" : ""}`}>
          {auction ? "경매" : "판매"}
        </span>
      </td>
      <td style={{ textAlign: "right" }} className="muted">
        {item.stock}
      </td>
      <td style={{ textAlign: "right" }}>
        {/* **「입찰가」를 앞에 둔다.** 뒤에 붙이면 그 행만 끝 글자가 달라져서
            우측 정렬을 해도 「원」이 세로로 안 맞는다. 숫자 열은 단위가 같은
            자리에 있어야 훑어 읽힌다. */}
        {auction && <span className="muted">입찰가 </span>}
        {formatWon(auction ? (item.currentBidPrice ?? item.price) : item.price)}
      </td>
      <td className="muted">{item.createdAt.slice(0, 10)}</td>
    </tr>
  );
}
