# Requirements Document

## Introduction

AstraGate là một LLM API Gateway SaaS dành cho developers và doanh nghiệp muốn truy cập nhiều nhà cung cấp mô hình ngôn ngữ lớn (LLM) thông qua một API thống nhất. Thay vì tự xây dựng proxy từ đầu, AstraGate sử dụng **LiteLLM Proxy** (open-source) làm core engine xử lý việc định tuyến và gọi API đến các provider (OpenAI, Anthropic, Groq, DeepSeek, Gemini, v.v.).

AstraGate đóng vai trò là **wrapper layer** đứng trước LiteLLM Proxy, cung cấp các tính năng kinh doanh mà LiteLLM không có sẵn:

- **Virtual Key Management**: Cấp phát và quản lý API key cho từng khách hàng
- **Credit & Billing**: Hệ thống credit prepaid tích hợp Stripe
- **Markup Engine**: Tính giá bán cao hơn giá gốc để tạo margin
- **Provider Balance Management**: Theo dõi số dư tài khoản provider, cảnh báo và dừng khi cần
- **Guardrails**: Lọc nội dung đầu vào/đầu ra cơ bản
- **Dashboard & Analytics**: Giao diện quản lý và theo dõi usage
- **Onboarding nhanh**: Khách hàng có thể bắt đầu sử dụng trong dưới 5 phút

**Phạm vi Phase 1 (MVP)**: Tập trung vào các tính năng đủ để khách hàng sử dụng và trả tiền. Ưu tiên provider miễn phí/rẻ (Groq, DeepSeek, Gemini Flash) để giảm chi phí vận hành ban đầu. Giao diện public hoàn toàn bằng tiếng Anh, phong cách global.

---

## Glossary

- **AstraGate**: Hệ thống LLM API Gateway SaaS được mô tả trong tài liệu này.
- **LiteLLM_Proxy**: Phần mềm open-source LiteLLM Proxy, đóng vai trò core engine xử lý việc gọi API đến các LLM provider.
- **Virtual_Key**: API key do AstraGate cấp phát cho khách hàng, dùng để xác thực và theo dõi usage.
- **Customer**: Người dùng hoặc tổ chức đã đăng ký tài khoản AstraGate và sử dụng dịch vụ.
- **Admin**: Người vận hành AstraGate (solo founder), có quyền truy cập toàn bộ hệ thống.
- **Credit**: Đơn vị tiền tệ nội bộ của AstraGate, được nạp trước (prepaid) và trừ dần theo usage.
- **Credit_Balance**: Số dư credit hiện tại của một Customer.
- **Provider**: Nhà cung cấp LLM (OpenAI, Anthropic, Groq, DeepSeek, Google Gemini, v.v.).
- **Provider_Account**: Tài khoản của AstraGate tại một Provider, có số dư riêng.
- **Provider_Balance**: Số dư tài khoản của AstraGate tại một Provider cụ thể.
- **Markup**: Tỷ lệ phần trăm hoặc hệ số nhân được cộng thêm vào giá gốc của Provider để tạo margin.
- **Token_Cost**: Chi phí tính theo số lượng token được xử lý bởi một LLM model.
- **Guardrail**: Cơ chế lọc và kiểm soát nội dung đầu vào (prompt) và đầu ra (response).
- **Dashboard**: Giao diện web quản lý dành cho Admin và Customer.
- **Usage_Record**: Bản ghi chi tiết về một lần gọi API, bao gồm model, token count, cost, timestamp.
- **Stripe**: Cổng thanh toán được sử dụng để xử lý nạp credit.
- **Webhook**: HTTP callback từ Stripe để thông báo kết quả thanh toán.
- **Hard_Stop**: Trạng thái dừng hoàn toàn việc gọi API đến một Provider khi Provider_Balance xuống dưới ngưỡng tối thiểu.
- **Buffer_Threshold**: Ngưỡng Provider_Balance tối thiểu để kích hoạt cảnh báo.
- **Exact_Cache**: Cơ chế cache kết quả LLM cho các request có prompt giống hệt nhau.
- **Fallback**: Cơ chế tự động chuyển sang Provider/model khác khi Provider chính gặp lỗi.
- **Rate_Limit**: Giới hạn số lượng request trong một khoảng thời gian nhất định.
- **Onboarding**: Quá trình đăng ký và thiết lập ban đầu của Customer.

---

## Requirements

### Requirement 1: Kiến Trúc Tích Hợp LiteLLM Proxy

**User Story:** Là Admin, tôi muốn AstraGate sử dụng LiteLLM Proxy làm core engine để không phải tự xây dựng và bảo trì logic proxy.

**Luồng xử lý request:**

```
Customer → AstraGate (Auth + Credit Check + Guardrail) → LiteLLM Proxy (HTTP nội bộ) → LLM Provider
         ←────────────── AstraGate (ghi Usage_Record + trừ Credit) ←──────────────────────────────
```

#### Acceptance Criteria

1. THE AstraGate SHALL chỉ giao tiếp với LiteLLM_Proxy qua HTTP calls đến các endpoint `/v1/chat/completions`, `/v1/embeddings`, `/v1/models`. AstraGate KHÔNG gọi trực tiếp đến bất kỳ LLM Provider nào.
2. WHEN Customer gửi request, THE AstraGate SHALL thực hiện tuần tự: xác thực Virtual_Key → kiểm tra Credit_Balance → áp dụng Guardrail → forward request đến LiteLLM_Proxy.
3. WHEN LiteLLM_Proxy trả về response thành công, THE AstraGate SHALL ghi Usage_Record, trừ Credit (sau Markup), rồi trả response về Customer.
4. THE AstraGate SHALL deploy LiteLLM_Proxy như một internal service, không expose ra internet. LiteLLM_Proxy giữ toàn bộ Provider API key; AstraGate không lưu trữ hoặc truy cập Provider API key trực tiếp.
5. WHEN Customer gửi request với `stream: true`, THE AstraGate SHALL proxy SSE stream từ LiteLLM_Proxy về Customer theo từng chunk, không buffer toàn bộ response.
6. IF LiteLLM_Proxy không phản hồi trong vòng 30 giây, THEN THE AstraGate SHALL hủy request và trả về HTTP 504 cho Customer.

---

### Requirement 2: Quản Lý Virtual Key

**User Story:** Là Customer, tôi muốn nhận một API key riêng từ AstraGate, để tôi có thể gọi LLM API mà không cần quản lý key của từng Provider.

#### Acceptance Criteria

1. WHEN Customer hoàn tất Onboarding, THE AstraGate SHALL tự động tạo một Virtual_Key mặc định cho Customer đó.
2. THE AstraGate SHALL cho phép Customer tạo tối đa 10 Virtual_Key trên mỗi tài khoản trong Phase 1.
3. WHEN Customer tạo Virtual_Key, THE AstraGate SHALL cho phép Customer đặt tên, mô tả, và giới hạn Rate_Limit tùy chọn cho từng key.
4. WHEN Customer thu hồi một Virtual_Key, THE AstraGate SHALL vô hiệu hóa key đó trong vòng 5 giây và từ chối tất cả request sử dụng key đó sau thời điểm thu hồi.
5. THE AstraGate SHALL lưu trữ Virtual_Key dưới dạng hash (không lưu plaintext), chỉ hiển thị giá trị đầy đủ một lần duy nhất tại thời điểm tạo.
6. WHEN một request đến với Virtual_Key không hợp lệ hoặc đã bị thu hồi, THE AstraGate SHALL trả về HTTP 401 với thông báo lỗi rõ ràng.
7. THE AstraGate SHALL ghi lại thời gian tạo, thời gian sử dụng cuối cùng, và tổng usage của mỗi Virtual_Key.

---

### Requirement 3: Hệ Thống Credit và Billing

**User Story:** Là Customer, tôi muốn nạp credit trước và sử dụng dần, để tôi kiểm soát được chi phí sử dụng LLM API.

**Flow xử lý credit cho mỗi request:**
1. Ước tính max cost từ tham số `max_tokens` × đơn giá model (sau markup)
2. Nếu `Credit_Balance < estimated_max_cost` → từ chối HTTP 402, không gọi LiteLLM_Proxy
3. Nếu đủ → hold credit = estimated_max_cost → gọi LiteLLM_Proxy
4. Nhận actual token count → settle: trừ `actual_token_cost × (1 + markup_rate)` → release phần dư

#### Acceptance Criteria

1. THE AstraGate SHALL sử dụng mô hình prepaid credit: Customer nạp credit trước, hệ thống trừ credit sau mỗi lần gọi API thành công.
2. WHEN Customer gửi request, THE AstraGate SHALL ước tính max cost dựa trên `max_tokens` và đơn giá model sau markup trước khi gọi LiteLLM_Proxy.
3. IF Credit_Balance không đủ để cover estimated max cost, THEN THE AstraGate SHALL từ chối request với HTTP 402 và không gọi LiteLLM_Proxy.
4. WHEN Credit_Balance đủ, THE AstraGate SHALL hold credit tương ứng estimated max cost, gọi LiteLLM_Proxy, rồi settle với actual cost và release phần dư.
5. THE AstraGate SHALL tính actual billed amount theo công thức: `billed_amount = actual_token_cost × (1 + markup_rate)`.
6. WHEN Customer khởi tạo nạp credit, THE AstraGate SHALL chuyển hướng đến Stripe Checkout, tối thiểu $5 USD.
7. WHEN Stripe gửi Webhook `payment_intent.succeeded`, THE AstraGate SHALL cộng credit vào Credit_Balance trong vòng 30 giây.
8. WHEN Stripe gửi Webhook thất bại, THE AstraGate SHALL ghi log lỗi và không thay đổi Credit_Balance.
9. WHEN Credit_Balance còn dưới 20% so với lần nạp gần nhất, THE AstraGate SHALL gửi email cảnh báo đến Customer.
10. THE AstraGate SHALL lưu lịch sử tất cả giao dịch nạp credit với trạng thái, số tiền, và timestamp.

---

### Requirement 4: Markup Engine

**User Story:** Là Admin, tôi muốn thiết lập markup linh hoạt trên từng model hoặc Provider, để AstraGate tạo ra margin kinh doanh.

**Thứ tự ưu tiên (cao → thấp):** Model-level → Provider-level → Global default

**Công thức:** `billed_amount = base_cost × (1 + markup_rate)` — `markup_rate` từ 0.0 đến 5.0 (0%–500%)

#### Acceptance Criteria

1. THE Admin SHALL có khả năng thiết lập Markup cho từng model cụ thể, từng Provider, hoặc toàn hệ thống (global default).
2. WHEN AstraGate tính giá bán, THE AstraGate SHALL áp dụng markup theo thứ tự ưu tiên: model-level → provider-level → global default, lấy cấp đầu tiên tìm thấy.
3. THE AstraGate SHALL tính giá bán theo công thức `billed_amount = base_cost × (1 + markup_rate)` với `markup_rate` từ 0.0 đến 5.0.
4. THE AstraGate SHALL cho phép Admin thiết lập markup = 0% (pass-through pricing) để bán đúng giá gốc Provider.
5. WHEN Admin cập nhật Markup, THE AstraGate SHALL áp dụng giá trị mới cho tất cả request từ thời điểm đó trở đi, không ảnh hưởng đến Usage_Record đã ghi.
6. THE AstraGate SHALL hiển thị trên Admin Dashboard cho từng model: giá gốc Provider, markup rate đang áp dụng (kèm cấp đang dùng: model/provider/global), và giá bán sau markup.

---

### Requirement 5: Quản Lý Provider Balance

**User Story:** Là Admin, tôi muốn theo dõi và kiểm soát số dư tài khoản tại các Provider, để tránh tình trạng âm vốn hoặc gián đoạn dịch vụ.

**Cơ chế hai ngưỡng:**
- `warning_threshold`: gửi email alert + hiển thị warning trên Dashboard
- `hard_stop_threshold`: kích hoạt Hard Stop — dừng toàn bộ request đến Provider đó

**Cập nhật balance:** Admin nhập thủ công (Provider không có API query balance). Hệ thống tự trừ actual cost (giá gốc, không markup) sau mỗi request thành công.

#### Acceptance Criteria

1. THE Admin SHALL có khả năng nhập và cập nhật Provider_Balance thủ công cho từng Provider_Account.
2. THE AstraGate SHALL tự động trừ actual cost (giá gốc Provider, không markup) khỏi Provider_Balance sau mỗi request thành công, và ghi log với request ID, amount, Provider_Balance trước/sau, timestamp.
3. THE Admin SHALL có khả năng thiết lập `warning_threshold` và `hard_stop_threshold` riêng cho từng Provider_Account, tối thiểu $1 USD mỗi ngưỡng.
4. WHEN Provider_Balance xuống dưới `warning_threshold`, THE AstraGate SHALL gửi email cảnh báo đến Admin và hiển thị trạng thái warning trên Admin Dashboard, tối đa 1 email/giờ cho cùng một Provider_Account.
5. WHEN Provider_Balance xuống dưới `hard_stop_threshold`, THE AstraGate SHALL kích hoạt Hard_Stop: từ chối tất cả request đến Provider đó, gửi email alert ngay lập tức, và ghi log sự kiện với timestamp và balance tại thời điểm đó.
6. WHEN Hard_Stop được kích hoạt và có Fallback Provider được cấu hình, THE AstraGate SHALL tự động route request sang Fallback Provider.
7. IF Hard_Stop được kích hoạt và không có Fallback Provider, THEN THE AstraGate SHALL trả về HTTP 503 cho Customer.
8. THE Hard_Stop SHALL chỉ được gỡ bỏ khi Admin manually cập nhật Provider_Balance và xác nhận trên Admin Dashboard.
9. THE AstraGate SHALL hiển thị trên Admin Dashboard cho từng Provider_Account: Provider_Balance hiện tại, estimated burn rate ($/giờ và $/ngày), estimated days remaining, và trạng thái (normal / warning / hard_stop).
10. WHEN Admin cập nhật Provider_Balance thủ công, THE AstraGate SHALL ghi log với giá trị cũ, giá trị mới, và timestamp.

---

### Requirement 6: Định Tuyến và Fallback

**User Story:** Là Customer, tôi muốn request của mình được tự động chuyển sang model/provider khác khi provider chính gặp sự cố, để dịch vụ không bị gián đoạn.

#### Acceptance Criteria

1. THE AstraGate SHALL cấu hình LiteLLM_Proxy với danh sách Fallback model cho mỗi model chính, theo thứ tự ưu tiên do Admin định nghĩa.
2. WHEN LiteLLM_Proxy báo cáo lỗi từ Provider chính (HTTP 429, 500, 503, hoặc timeout), THE AstraGate SHALL để LiteLLM_Proxy tự động thử Fallback model tiếp theo trong danh sách.
3. WHEN tất cả Fallback model đều thất bại, THE AstraGate SHALL trả về HTTP 503 với thông báo lỗi rõ ràng cho Customer.
4. THE AstraGate SHALL ưu tiên cấu hình các Provider miễn phí hoặc chi phí thấp (Groq, DeepSeek, Gemini Flash) làm lựa chọn mặc định trong Phase 1.
5. THE AstraGate SHALL ghi lại Provider và model thực tế được sử dụng trong mỗi Usage_Record, kể cả khi Fallback được kích hoạt.

---

### Requirement 7: Exact Cache

**User Story:** Là Admin, tôi muốn cache kết quả cho các prompt giống hệt nhau, để giảm chi phí Provider và tăng tốc độ phản hồi.

#### Acceptance Criteria

1. THE AstraGate SHALL kích hoạt tính năng Exact_Cache của LiteLLM_Proxy để cache response cho các request có cùng model, messages, và parameters.
2. WHEN một request khớp với Exact_Cache, THE AstraGate SHALL trả về response từ cache mà không gọi Provider, và ghi Usage_Record với `cache_hit = true`.
3. WHEN một request có cache_hit, THE AstraGate SHALL tính Token_Cost bằng 0 (không trừ credit của Customer cho cache hit).
4. THE AstraGate SHALL sử dụng Redis làm backend cache cho Exact_Cache với TTL mặc định là 1 giờ.
5. THE Admin SHALL có khả năng bật/tắt Exact_Cache toàn hệ thống từ Admin Dashboard.
6. THE AstraGate SHALL hiển thị tỷ lệ cache hit trong Dashboard Analytics.

---

### Requirement 8: Guardrails Cơ Bản

**User Story:** Là Admin, tôi muốn lọc các prompt và response vi phạm chính sách sử dụng, để bảo vệ dịch vụ khỏi bị lạm dụng.

#### Acceptance Criteria

1. THE AstraGate SHALL kiểm tra prompt đầu vào của Customer trước khi gửi đến LiteLLM_Proxy theo danh sách từ khóa bị cấm do Admin cấu hình.
2. WHEN prompt chứa từ khóa bị cấm, THE AstraGate SHALL từ chối request với HTTP 400 và thông báo vi phạm chính sách, không gọi LiteLLM_Proxy.
3. THE AstraGate SHALL kiểm tra response từ LiteLLM_Proxy theo danh sách từ khóa bị cấm trước khi trả về cho Customer.
4. WHEN response chứa từ khóa bị cấm, THE AstraGate SHALL thay thế response bằng thông báo lỗi chuẩn và ghi log sự kiện.
5. THE Admin SHALL có khả năng thêm, sửa, xóa từ khóa trong danh sách Guardrail từ Admin Dashboard.
6. THE AstraGate SHALL ghi log tất cả sự kiện Guardrail bị kích hoạt với timestamp, Virtual_Key, và nội dung vi phạm (đã được truncate để bảo mật).
7. WHERE Admin bật tính năng rate limiting per Virtual_Key, THE AstraGate SHALL từ chối request vượt quá Rate_Limit với HTTP 429.

---

### Requirement 9: Onboarding Nhanh

**User Story:** Là Customer mới, tôi muốn bắt đầu sử dụng AstraGate trong dưới 5 phút, để không mất thời gian vào việc thiết lập phức tạp.

#### Acceptance Criteria

1. THE AstraGate SHALL cho phép Customer đăng ký tài khoản bằng email và mật khẩu hoặc OAuth (Google).
2. WHEN Customer hoàn tất đăng ký, THE AstraGate SHALL tự động tạo Virtual_Key mặc định và hiển thị ngay trên màn hình.
3. THE AstraGate SHALL cung cấp trang "Quick Start" với code snippet mẫu bằng Python, Node.js, và cURL để Customer copy và chạy ngay.
4. THE AstraGate SHALL cung cấp $1 credit miễn phí cho Customer mới để thử nghiệm, không yêu cầu thông tin thanh toán.
5. WHEN Customer hoàn tất đăng ký và nhận Virtual_Key, THE AstraGate SHALL hoàn thành toàn bộ quá trình trong dưới 5 phút tính từ lúc Customer bắt đầu điền form đăng ký.
6. THE AstraGate SHALL gửi email chào mừng với hướng dẫn bắt đầu và link đến tài liệu API trong vòng 2 phút sau khi đăng ký thành công.

---

### Requirement 10: Customer Dashboard

**User Story:** Là Customer, tôi muốn xem Credit_Balance, lịch sử usage, và quản lý Virtual_Key từ một giao diện web, để tôi kiểm soát được việc sử dụng dịch vụ.

#### Acceptance Criteria

1. THE AstraGate SHALL cung cấp Customer Dashboard với các trang: Overview, API Keys, Usage, Billing.
2. THE AstraGate SHALL hiển thị Credit_Balance hiện tại trên trang Overview, cập nhật trong vòng 60 giây sau mỗi lần gọi API.
3. THE AstraGate SHALL hiển thị biểu đồ usage theo ngày trong 30 ngày gần nhất trên trang Usage, bao gồm số request, tổng token, và tổng chi phí.
4. THE AstraGate SHALL cho phép Customer lọc Usage_Record theo Virtual_Key, model, và khoảng thời gian trên trang Usage.
5. THE AstraGate SHALL hiển thị danh sách Virtual_Key với trạng thái (active/revoked), ngày tạo, và usage tổng trên trang API Keys.
6. THE AstraGate SHALL cho phép Customer tạo, đặt tên, và thu hồi Virtual_Key trực tiếp từ trang API Keys.
7. THE AstraGate SHALL hiển thị lịch sử giao dịch nạp credit với số tiền, trạng thái, và timestamp trên trang Billing.
8. THE AstraGate SHALL cung cấp nút "Add Credits" trên trang Billing để Customer khởi tạo thanh toán Stripe.

---

### Requirement 11: Admin Dashboard

**User Story:** Là Admin, tôi muốn có giao diện quản lý toàn hệ thống, để tôi vận hành AstraGate hiệu quả với tư cách solo founder.

#### Acceptance Criteria

1. THE AstraGate SHALL cung cấp Admin Dashboard với các trang: Overview, Customers, Providers, Models, Guardrails, Settings.
2. THE AstraGate SHALL hiển thị trên trang Overview: tổng số Customer, tổng revenue trong ngày/tháng, tổng request trong ngày, và trạng thái của từng Provider.
3. THE AstraGate SHALL hiển thị danh sách Customer với Credit_Balance, tổng usage, và trạng thái tài khoản trên trang Customers.
4. THE AstraGate SHALL cho phép Admin xem chi tiết usage của từng Customer, bao gồm từng Usage_Record.
5. THE AstraGate SHALL hiển thị Provider_Balance và trạng thái (normal/warning/hard_stop) của từng Provider_Account trên trang Providers.
6. THE AstraGate SHALL cho phép Admin cập nhật Provider_Balance thủ công và thiết lập Buffer_Threshold trên trang Providers.
7. THE AstraGate SHALL cho phép Admin thiết lập Markup cho từng model và Provider trên trang Models.
8. THE AstraGate SHALL cho phép Admin quản lý danh sách từ khóa Guardrail trên trang Guardrails.
9. THE AstraGate SHALL bảo vệ Admin Dashboard bằng xác thực riêng biệt, không cho phép Customer truy cập.

---

### Requirement 12: Usage Analytics và Observability

**User Story:** Là Admin, tôi muốn theo dõi chi tiết usage của toàn hệ thống, để tôi phát hiện vấn đề và tối ưu chi phí vận hành.

#### Acceptance Criteria

1. THE AstraGate SHALL ghi Usage_Record cho mỗi request thành công, bao gồm: Virtual_Key ID, model, Provider, input_tokens, output_tokens, cost_usd (giá gốc), billed_amount (sau markup), latency_ms, cache_hit, timestamp.
2. THE AstraGate SHALL ghi log lỗi cho mỗi request thất bại, bao gồm: error_code, error_message, Provider, model, timestamp.
3. THE AstraGate SHALL tính toán và hiển thị các chỉ số tổng hợp: tổng request, tổng token, tổng revenue, tỷ lệ lỗi, tỷ lệ cache hit theo ngày/tuần/tháng.
4. THE AstraGate SHALL lưu trữ Usage_Record trong tối thiểu 90 ngày.
5. WHEN tỷ lệ lỗi của một Provider vượt quá 10% trong 5 phút liên tiếp, THE AstraGate SHALL ghi cảnh báo vào log hệ thống.
6. THE AstraGate SHALL cho phép Admin export Usage_Record ra file CSV theo khoảng thời gian tùy chọn.

---

### Requirement 13: API Tương Thích OpenAI

**User Story:** Là Customer, tôi muốn AstraGate có API endpoint tương thích với OpenAI SDK, để tôi không cần thay đổi code hiện tại.

#### Acceptance Criteria

1. THE AstraGate SHALL expose endpoint `POST /v1/chat/completions` tương thích với OpenAI Chat Completions API specification.
2. THE AstraGate SHALL expose endpoint `POST /v1/embeddings` tương thích với OpenAI Embeddings API specification.
3. THE AstraGate SHALL expose endpoint `GET /v1/models` trả về danh sách model hiện có trên AstraGate.
4. WHEN Customer gửi request với header `Authorization: Bearer {virtual_key}`, THE AstraGate SHALL xác thực Virtual_Key và xử lý request.
5. THE AstraGate SHALL hỗ trợ streaming response (`stream: true`) thông qua Server-Sent Events (SSE) theo chuẩn OpenAI.
6. WHEN Customer gửi request với tham số `model` không tồn tại trên AstraGate, THE AstraGate SHALL trả về HTTP 404 với thông báo lỗi theo định dạng OpenAI error format.
7. THE AstraGate SHALL trả về response với cấu trúc JSON giống hệt OpenAI API response, bao gồm `usage.prompt_tokens`, `usage.completion_tokens`, và `usage.total_tokens`.

---

### Requirement 14: Bảo Mật và Xác Thực

**User Story:** Là Admin, tôi muốn hệ thống có các biện pháp bảo mật cơ bản, để bảo vệ dữ liệu Customer và ngăn chặn lạm dụng.

#### Acceptance Criteria

1. THE AstraGate SHALL mã hóa tất cả dữ liệu truyền tải bằng HTTPS/TLS 1.2 trở lên.
2. THE AstraGate SHALL lưu trữ mật khẩu Customer dưới dạng hash sử dụng bcrypt với cost factor tối thiểu là 12.
3. THE AstraGate SHALL lưu trữ Virtual_Key dưới dạng SHA-256 hash, không lưu plaintext.
4. THE AstraGate SHALL lưu trữ API key của Provider (OpenAI key, Groq key, v.v.) dưới dạng mã hóa trong database, không lưu plaintext.
5. THE AstraGate SHALL giới hạn tối đa 10 lần đăng nhập thất bại liên tiếp trong 15 phút trước khi khóa tài khoản tạm thời.
6. THE AstraGate SHALL xác thực chữ ký Stripe Webhook (`Stripe-Signature` header) trước khi xử lý bất kỳ Webhook event nào.
7. WHEN một Virtual_Key được sử dụng từ nhiều hơn 10 địa chỉ IP khác nhau trong 1 giờ, THE AstraGate SHALL ghi cảnh báo vào log bảo mật.

---

### Requirement 15: Kiến Trúc Scalable cho Solo Founder

**User Story:** Là Admin (solo founder), tôi muốn hệ thống có kiến trúc đơn giản nhưng có thể scale, để tôi vận hành được một mình và mở rộng khi cần.

#### Acceptance Criteria

1. THE AstraGate SHALL triển khai trên kiến trúc container (Docker) để dễ dàng deploy và scale.
2. THE AstraGate SHALL sử dụng PostgreSQL làm primary database cho tất cả dữ liệu nghiệp vụ.
3. THE AstraGate SHALL sử dụng Redis cho Exact_Cache và session management.
4. THE AstraGate SHALL xử lý tối thiểu 100 concurrent request mà không degradation đáng kể trong Phase 1.
5. THE AstraGate SHALL có health check endpoint `GET /health` trả về trạng thái của các dependency (database, Redis, LiteLLM_Proxy).
6. THE AstraGate SHALL ghi structured log (JSON format) để dễ dàng tích hợp với log aggregation service sau này.
7. IF database connection bị mất, THEN THE AstraGate SHALL trả về HTTP 503 cho tất cả request và tự động reconnect sau mỗi 5 giây.
