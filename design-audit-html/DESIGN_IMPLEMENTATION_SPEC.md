# Design Implementation Spec

## Bảng handoff

| Audit ID | Vấn đề | File/màn hình | Phương án HTML | Quy tắc responsive | Acceptance criteria |
|---|---|---|---|---|---|
| P1-01 + P1-06 | Card Local Models vỡ layout; selector không lọc capability | `index.html` → `screen-models`; `styles.css` → `.model-*`; `app.js` → download/detail/activation | Card có header cố định, badge active, `dl` key/value, footer, details; select chat/embedding loại trừ option sai capability; downloading có progress và cancel | ≥1181: 2 card/cột, metadata 3 cột; 721–1180: metadata 2 cột; ≤720: 1 card/cột; ≤480: key/value 2 cột, action xếp dọc | Không dính text; không page overflow; nhận diện model/capability/RAM/VRAM/active nhanh; không chọn được capability sai; embedding change luôn có impact preview |
| P1-02 | Knowledge tràn page, mất KPI/cột/CTA | `screen-knowledge`; `.kpi-grid`, `.knowledge-table-wrap`, `.knowledge-card-list`, `.bulk-toolbar` | KPI 4/2/1 cột; desktop table; tablet ẩn cột phụ; mobile card mở rộng; selection đồng bộ giữa table/card; bulk toolbar sticky | ≥1181: đủ 9 cột; 901–1180: ẩn “Lần học” và “Lỗi/ghi chú”; ≤720: mặc định card; người dùng vẫn có thể ép table trong container cuộn riêng; ≤480: KPI 1 cột | `body.scrollWidth === innerWidth`; KPI/CTA luôn truy cập được; chỉ table container được phép cuộn ngang; mobile đọc được MySQL/vector/size/action; bulk toolbar chỉ hiện khi có selection |
| P1-03 | Learning Job báo `Completed · 0%` | `screen-jobs`; `jobStates`; `renderJobState()` | Tách status, phase, progress, processed/total, last error; progress bar chỉ render khi có số tin cậy; action thay theo status | Job card 4 metric/cột desktop, 2 cột tablet, 1 cột mobile; contract table co vừa ở ≤480 | Completed luôn 100%; queued 0%; syncing 1–50%; embedding 51–99%; failed/paused giữ mốc cuối; unknown không render percentage; pause/resume/retry hoạt động |
| P1-04 | Offline/SSE stale vẫn báo LIVE | `screen-connection`; `connectionStates`; `setConnectionState()` | State machine Connecting/Live/Reconnecting/Offline/Session expired; tách API/SSE; last successful; stale warning; retry; login redirect | Meta 3 cột desktop, 1 cột ≤900; action xếp dọc ≤480 | LIVE chỉ sau SSE connected; offline/reconnecting không còn LIVE; snapshot cũ có timestamp; session expired không retry SSE; retry thủ công phục hồi được |
| P1-05 | Topbar/modal tràn tại 360 px, mất nút đóng | `screen-modal`; `#prototypeModal`; `.prototype-modal*`; `openModal()`/`closeModal()` | Dialog grid header/body/footer; body scroll nội bộ; backdrop click; close/cancel/confirm; focus trap; focus restore | Desktop: `min(720px, 100vw - 40px)`, max-height viewport - 40px; ≤480: full-screen 100dvh, header/footer cố định, body cuộn; button min 44px | Body đúng 360 px; close nằm trong viewport; close target ≥44×44; đóng bằng nút/backdrop/ESC; Tab/Shift+Tab không thoát; focus trở về trigger |

## Design system

### Typography

- Logo và main page title: `Peter Obscure`, weight 400.
- Navigation, panel title, body, table, metric, badge, input và button: `Darley Sans`.
- Fallback: `Arial Black` cho display, `Arial Narrow`/sans-serif cho UI.
- Không dùng font display cho dữ liệu vận hành dài.

### Color tokens

| Token | Giá trị | Vai trò |
|---|---|---|
| `--ink` | `#090909` | Text, border, hard shadow, header |
| `--paper` | `#f3efdf` | Canvas nền giấy |
| `--paper-2` | `#fffdf5` | Surface chính |
| `--teal` | `#00c8c8` | Primary action, selected, progress |
| `--magenta` | `#ef00c8` | Accent/hard-shadow |
| `--yellow` | `#ffd51f` | Warning, active control, stale |
| `--green` | `#16c98d` | Live/completed |
| `--red` | `#c41252` | P1/offline/failed/expired |

### Shape và elevation

- Border chính: 3 px đen.
- Border phụ: 2 px đen.
- Corner: vuông; không bo card, input hoặc button.
- Hard shadow chính: 7×7 px.
- Hard shadow nhỏ: 4×4 px.
- Không dùng gradient, glass, soft shadow hoặc card bo tròn kiểu SaaS.

### Spacing

- Canvas: `clamp(18px, 2.5vw, 34px)` desktop; 14–16 px mobile.
- Khoảng section chính: 24–26 px.
- Panel header: 17–18 px.
- Card metadata: 8 px gap, 10 px cell padding.
- Touch/control target: tối thiểu 44 px.
- Modal viewport margin: 20 px desktop, 0 px khi full-screen mobile.

## Breakpoints

| Breakpoint | Mục tiêu |
|---|---|
| `>1180px` | Desktop đầy đủ; summary 2 cột; model grid 2 cột; Knowledge đủ cột |
| `901–1180px` | Laptop/tablet ngang; header rút gọn; Knowledge ẩn cột phụ |
| `721–900px` | Tablet; layout chính 1 cột; KPI 2 cột; form wrap |
| `481–720px` | Mobile lớn; Knowledge chuyển card; action xếp dọc khi cần |
| `≤480px` | Mobile nhỏ; nav 3 cột × 2 hàng; KPI 1 cột; modal full-screen; metrics 1 cột |

## Knowledge: cột ẩn và dữ liệu mở rộng

- Desktop: Selection, Nguồn, Trạng thái, MySQL, Vector, Dung lượng, Lần học,
  Lỗi/ghi chú, Thao tác.
- 1024/tablet: giữ Selection, Nguồn, Trạng thái, MySQL, Vector, Dung lượng, Thao
  tác; ẩn Lần học và Lỗi/ghi chú.
- Mobile mặc định: card header có selection/source/status; summary có
  MySQL/vector/dung lượng; mở chi tiết để xem lần học, lỗi/ghi chú và action.
- Chuyển Table/Card là control prototype, không làm mất selection.
- Bulk toolbar `position: sticky; bottom: 12px`; chỉ hiện khi selection > 0.

## Learning progress contract

| Status/phase | Progress hợp lệ | Frontend behavior |
|---|---:|---|
| `queued` | `0` | Hiển thị “Chờ worker”, không animation giả |
| `running/syncing` | `1–50` | Dựa trên `synced_messages / total_messages` |
| `running/embedding` | `51–99` | Dựa trên `vectors_created / total_vectors` |
| `completed` | `100` | Normalize về 100; không chấp nhận completed dưới 100 |
| `failed` | Mốc cuối | Giữ progress gần nhất + `last_error` + Retry |
| `paused` | Mốc cuối | Giữ progress gần nhất + Resume |
| unknown/legacy | `null` | Không render bar hoặc `%`; ghi thiếu dữ liệu |

Backend response đề xuất:

```json
{
  "status": "running",
  "phase": "embedding",
  "progress": 78,
  "processed": 6310,
  "total": 10420,
  "synced_messages": 12840,
  "vectors_created": 6310,
  "duration_ms": 348000,
  "last_error": null,
  "updated_at": "2026-07-29T04:30:00Z"
}
```

Frontend phải validate:

- `completed && progress !== 100`: normalize 100 hoặc ẩn progress và log contract
  mismatch; tuyệt đối không render Completed · 0%.
- Không có mẫu số tin cậy: `progress = null`.
- `failed/paused`: không reset progress về 0.

## Connection state logic

| State | Điều kiện | Nhãn shell | Dữ liệu | CTA |
|---|---|---|---|---|
| Connecting | Bắt đầu session/API/SSE | CONNECTING | Chưa coi là realtime | Chờ |
| Live | `EventSource.onopen` + heartbeat hợp lệ | LIVE | Cho phép snapshot mới | Không |
| Reconnecting | SSE error, network chưa xác nhận offline | RECONNECTING | Giữ snapshot + stale timestamp | Retry |
| Offline | `navigator.onLine === false` hoặc API thất bại liên tiếp | OFFLINE | Giữ snapshot, khóa write | Retry thủ công |
| Session expired | API/SSE trả 401/419 | SESSION EXPIRED | Không retry bằng session cũ | Về đăng nhập |

Yêu cầu backend/SSE:

- Heartbeat hoặc snapshot timestamp.
- Reason code khi disconnect.
- Status code phân biệt offline, backend unavailable và expired session.
- Không yêu cầu gửi secret/cookie vào UI.

## Modal behavior và accessibility

- `role="dialog"`, `aria-modal="true"`, `aria-labelledby`, `aria-describedby`.
- Focus ban đầu ở nút Đóng.
- Tab ở control cuối quay về control đầu; Shift+Tab ở control đầu quay về control
  cuối.
- ESC, backdrop, Đóng và Hủy đều đóng dialog.
- Sau khi đóng, focus quay về trigger.
- Header/footer không cuộn; `.prototype-modal-body` là vùng cuộn duy nhất.
- Mobile dài dùng full-screen; nội dung ngắn vẫn dùng cùng shell an toàn.
- Mọi button/input/select có `:focus-visible` tương phản cao.
- `prefers-reduced-motion` giảm transition/animation.

## Frontend-only và phần cần backend

### Frontend có thể triển khai ngay

- Responsive grid/card/table và cột ẩn.
- State component loading/error/empty/downloading.
- Bulk selection/sticky toolbar.
- Progress rendering/normalization guard.
- SSE status presentation, stale warning và timestamp.
- Modal sizing, focus trap, ESC, backdrop, focus restore.
- Capability filter nếu API đã trả metadata chuẩn.

### Cần backend/API hỗ trợ

- Capability chuẩn hóa: chat, embedding, tool-use, context, compatibility reason.
- Activation impact preview, số nguồn/vector bị ảnh hưởng và yêu cầu re-index.
- Learning job contract đầy đủ.
- SSE heartbeat, last snapshot, reconnect reason và expired-session signal.
- Download model progress stream, cancel endpoint và terminal status.
- Không đưa mock data của prototype vào operational view.
