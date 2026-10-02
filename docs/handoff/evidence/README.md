# Evidence ledger

Chưa có evidence implementation. Các file task-ID.md được tạo khi task thực hiện; tài liệu/150 test audit chỉ là baseline. Không tạo record Pass từ nội dung plan.

Mỗi task dùng template sau; reviewer và coordinator cập nhật checklist/status.json/roadmap.csv cùng ID.

```markdown
# ID — title
Status: InReview / Verified / Blocked
Commit / branch / worktree:
Dependencies (IDs + verified commit):
Spec / contract version:
Environment (OS/runtime/DB/browser):
Artifact path + SHA-256 (nếu có):

## Change
Behavior trước/sau, file ownership và interface thay đổi.

## Red evidence
Test name, command riêng, exit code, expected failure và sanitized output.
Environment error không được tính behavior red.

## Verification
Command / exit code / pass-fail-skipped counts / sanitized log location.
Acceptance matrix: mỗi requirement có evidence tương ứng.
Negative cases, regression scope, screenshot/artifact/VM/UAT nếu task yêu cầu.

## Review
Reviewer identity/role khác implementer, commit được review, findings và disposition.
Coordinator integration commit và verification sau integration.

## Limits / blockers
Case chưa chạy, đầu vào còn thiếu, known limitation và next action.
Không secret/OTP/raw account content trong evidence.
```

Red-green chỉ áp dụng test behavior mới thực sự cần chứng minh. Với baseline/checkout/font inventory/cleanup, lưu before-after evidence phù hợp; không chế failing test để đủ thủ tục. Các task B00/U01/R01 có checklist hành chính phải phân biệt rõ behavior test và inspection.

Với task code, Verified yêu cầu acceptance pass, review findings chặn release đã xử lý và regression liên quan pass. Với UAT/soak, ghi participant/workload/thời lượng thực đã đo. Nếu artifact đổi sau QA, reviewer xác định test nào cần chạy lại; không gắn hash cũ vào bản mới.
