# BÁO CÁO KIỂM THỬ HỆ THỐNG ASTRAGATE
**Ngày báo cáo:** 11/06/2026  
**QC Agent:** Quality Control Agent  
**Trạng thái chung:** **100% PASS** (179/179 test cases)

---

## 1. DANH SÁCH USE CASE ĐÃ KIỂM THỬ

Hệ thống test suite của AstraGate bao gồm 179 bài test kiểm tra toàn bộ các khía cạnh kỹ thuật và luồng nghiệp vụ của backend.

| STT | Use Case / Phân hệ | Đường dẫn tệp kiểm thử | Số lượng test | Trạng thái |
| :--- | :--- | :--- | :---: | :---: |
| 1 | **Gateway Router** | [test_gateway_router.py](file:///Users/khoaknd/Documents/AI Vibe/astragate/backend/tests/test_gateway_router.py) | 8 | **PASS** |
| 2 | **Admin Markup** | [test_admin_markup.py](file:///Users/khoaknd/Documents/AI Vibe/astragate/backend/tests/test_admin_markup.py) | 16 | **PASS** |
| 3 | **Guardrail Service** | [test_guardrail_service.py](file:///Users/khoaknd/Documents/AI Vibe/astragate/backend/tests/test_guardrail_service.py) | 27 | **PASS** |
| 4 | **LiteLLM Client** | [test_litellm_client.py](file:///Users/khoaknd/Documents/AI Vibe/astragate/backend/tests/test_litellm_client.py) | 19 | **PASS** |
| 5 | **Markup Service** | [test_markup_service.py](file:///Users/khoaknd/Documents/AI Vibe/astragate/backend/tests/test_markup_service.py) | 16 | **PASS** |
| 6 | **Post Processing Pipeline** | [test_post_process.py](file:///Users/khoaknd/Documents/AI Vibe/astragate/backend/tests/test_post_process.py) | 17 | **PASS** |
| 7 | **Provider Balance & Alerts** | [test_provider_balance.py](file:///Users/khoaknd/Documents/AI Vibe/astragate/backend/tests/test_provider_balance.py) | 30 | **PASS** |
| 8 | **Rate Limit (RPM/TPM)** | [test_rate_limit.py](file:///Users/khoaknd/Documents/AI Vibe/astragate/backend/tests/test_rate_limit.py) | 10 | **PASS** |
| 9 | **Virtual Key Authentication** | [test_virtual_key_auth.py](file:///Users/khoaknd/Documents/AI Vibe/astragate/backend/tests/test_virtual_key_auth.py) | 17 | **PASS** |
| 10 | **Virtual Key Service** | [test_virtual_key_service.py](file:///Users/khoaknd/Documents/AI Vibe/astragate/backend/tests/test_virtual_key_service.py) | 12 | **PASS** |
| 11 | **E2E Integration Lifecycle** | [test_e2e_integration.py](file:///Users/khoaknd/Documents/AI Vibe/astragate/backend/tests/test_e2e_integration.py) | 7 | **PASS** |

---

## 2. CHI TIẾT CÁC LỖI ĐÃ PHÁT HIỆN VÀ CÁCH KHẮC PHỤC

Trong lần chạy thử nghiệm đầu tiên, hệ thống ghi nhận **9 lỗi thất bại**. Dưới đây là phân tích chi tiết nguyên nhân và các hành động sửa lỗi đã được áp dụng:

### Lỗi 1: Mock Database Session thiếu hỗ trợ `db.execute` cho Provider
* **Hiện tượng:** 8 test case trong [test_post_process.py](file:///Users/khoaknd/Documents/AI Vibe/astragate/backend/tests/test_post_process.py) bị lỗi do hàm background task `post_process_usage` thoát sớm và ghi nhận log `post_process_missing_model_or_provider`.
* **Nguyên nhân:** Mã nguồn của `post_process_usage` đã thay đổi cách lấy dữ liệu `Provider` từ `db.get()` sang dùng truy vấn bi quan `select(Provider)...with_for_update()` chạy qua `db.execute()`. Tuy nhiên, mock database session (`_make_session`) dùng trong unit test của `test_post_process.py` chỉ giả lập hàm `db.get()`, trong khi hàm `db.execute()` được mock mặc định trả về `None`, khiến cho Provider luôn bị nhận định là không tồn tại.
* **Cách khắc phục:** Cập nhật hàm giả lập `_make_session` trong [test_post_process.py](file:///Users/khoaknd/Documents/AI Vibe/astragate/backend/tests/test_post_process.py) để hỗ trợ việc compile statement và phân tích chuỗi truy vấn thô. Nếu truy vấn có chứa target table là `provider`, `credit_account` hoặc `user`, mock session sẽ trả về đúng mock object mong muốn tương ứng.

### Lỗi 2: Sai lệch số lượng `db.execute.await_count` kiểm tra trong unit test
* **Hiện tượng:** Lỗi so khớp `db.execute.await_count` ở 3 bài test success path và cache hit path.
* **Nguyên nhân:**
  1. Do Provider được chuyển từ `db.get()` sang dùng `db.execute(provider_stmt)` (thêm 1 lượt gọi `db.execute`).
  2. Phân hệ mới phát triển thêm tính năng cảnh báo tài khoản người dùng có số dư thấp bằng cách truy vấn `CreditAccount` qua `db.execute(account_stmt)` (thêm 1 lượt gọi `db.execute`).
  Do đó, số lượt gọi thực tế lớn hơn so với các xác nhận `assert db.execute.await_count == ...` cũ trong unit test.
* **Cách khắc phục:** Điều chỉnh lại giá trị kiểm thử `await_count` trong [test_post_process.py](file:///Users/khoaknd/Documents/AI Vibe/astragate/backend/tests/test_post_process.py):
  * Đường dẫn thành công (Success path): Tăng từ `2` lên `3` lượt (Provider, CreditAccount, VirtualKey update).
  * Đường dẫn cache hit: Tăng từ `1` lên `2` lượt (Provider, VirtualKey update).

### Lỗi 3: Lấy nhầm câu lệnh SQL `FOR UPDATE` làm lệnh `UPDATE` trong unit test
* **Hiện tượng:** Bài test `test_virtual_key_update_includes_token_count` thất bại với lỗi `KeyError: 'total_tokens_1'`.
* **Nguyên nhân:** Để kiểm tra dữ liệu token được cập nhật chính xác cho Virtual Key, bài test thực hiện duyệt qua tất cả các cuộc gọi `db.execute` và so khớp chuỗi SQL có chứa `"UPDATE"`. Vì câu lệnh truy vấn Provider có chứa từ khóa `"FOR UPDATE"`, vòng lặp đã gán nhầm câu lệnh này làm lệnh UPDATE cần kiểm tra và thoát ra sớm. Lệnh select Provider không chứa tham số bind `total_tokens_1` dẫn tới lỗi `KeyError`.
* **Cách khắc phục:** Sửa điều kiện so khớp trong [test_post_process.py:L565](file:///Users/khoaknd/Documents/AI Vibe/astragate/backend/tests/test_post_process.py#L565) thành kiểm tra cụ thể chuỗi `"UPDATE virtual_keys"` để đảm bảo lấy đúng câu lệnh cập nhật số liệu của Virtual Key.

### Lỗi 4: Thử nghiệm E2E không ổn định (Flaky Test) do trễ luồng bất đồng bộ
* **Hiện tượng:** Test case `test_full_lifecycle_register_to_usage` trong [test_e2e_integration.py](file:///Users/khoaknd/Documents/AI Vibe/astragate/backend/tests/test_e2e_integration.py) thỉnh thoảng thất bại vì `total_count` của usage record trả về 0.
* **Nguyên nhân:** Bài test kiểm tra E2E thực hiện gửi request chat completions và sử dụng `await asyncio.sleep(2)` để đợi background task ghi nhận usage record. Tuy nhiên trong môi trường Docker, do tài nguyên hoặc xử lý email cảnh báo, background task có thể tốn hơn 5 giây để ghi nhận bản ghi thành công. Điều này làm cho việc sleep cứng 2 giây không đủ thời gian và làm test bị fail.
* **Cách khắc phục:** Thay thế lệnh sleep cứng 2 giây trong [test_e2e_integration.py](file:///Users/khoaknd/Documents/AI Vibe/astragate/backend/tests/test_e2e_integration.py) bằng cơ chế **Polling (vòng lặp truy vấn thử lại)** với khoảng thời gian tối đa 10 giây (lặp 20 lần, mỗi lần 0.5 giây). Ngay khi phát hiện bản ghi usage xuất hiện, luồng test sẽ tiếp tục ngay lập tức, vừa tăng độ bền bỉ (robustness) vừa giảm thời gian chờ thừa thãi.

---

## 3. KẾT LUẬN & ĐỀ XUẤT

> [!IMPORTANT]
> Toàn bộ 179 bài test của AstraGate hiện đã chạy thành công **100%** trong docker container. Các thay đổi về cấu trúc mock database trong unit test và kỹ thuật polling trong E2E test đã được kiểm chứng và đóng gói thành công vào container.

> [!TIP]
> Để tránh việc test suite bị hỏng trong các lần chạy CI/CD tương lai:
> 1. Nên giữ nguyên cơ chế polling cho các bài test E2E có gọi API thông qua background tasks.
> 2. Các thay đổi trong database schema hoặc ORM query (như thêm `with_for_update` hay thay đổi phương thức từ `db.get` sang `db.execute`) cần được đồng bộ cập nhật vào mock database engine `_make_session` tương ứng của tầng test.
