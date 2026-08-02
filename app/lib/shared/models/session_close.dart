/// `POST /chat/sessions/{id}/close` 的回應。欄位規格見 docs/api.md。
///
/// 200 只承諾此 session 不再接受新 turn、離線工作已可恢復地啟動；
/// **不承諾**一般生活事件已經整理完成（見 [batchStatus]）。
class SessionCloseResult {
  const SessionCloseResult({
    required this.sessionId,
    required this.status,
    required this.closedAt,
    required this.batchStatus,
  });

  final String sessionId;

  /// 固定為 `closed`。
  final String status;

  /// 進入 closed 的時間；重送 close 不會改變。
  final DateTime? closedAt;

  /// 離線 materialization 進度；可用值見 [SessionBatchStatus]。
  final String batchStatus;

  /// 一般生活事件已寫入，此時 `GET /events` 才看得到本段對話的內容。
  bool get isBatchCompleted => batchStatus == SessionBatchStatus.completed;

  factory SessionCloseResult.fromJson(Map<String, dynamic> json) =>
      SessionCloseResult(
        sessionId: json['session_id'] as String? ?? '',
        status: json['status'] as String? ?? '',
        closedAt: json['closed_at'] == null
            ? null
            : DateTime.tryParse(json['closed_at'] as String)?.toLocal(),
        batchStatus:
            json['batch_status'] as String? ?? SessionBatchStatus.pending,
      );
}

/// session 的離線批次狀態（api.md 共用 enum `SessionBatchStatus`）。
class SessionBatchStatus {
  const SessionBatchStatus._();

  static const String pending = 'pending';
  static const String processing = 'processing';
  static const String completed = 'completed';
  static const String failed = 'failed';
}

/// 摘要的資料完整度（api.md 共用 enum `SummaryDataStatus`）。
class SummaryDataStatus {
  const SummaryDataStatus._();

  /// 相關 session 都已關閉且批次完成——摘要涵蓋當日全部資料。
  static const String complete = 'complete';

  /// 仍有 session 未收斂或批次未完成——UI 應提示「還有對話整理中」。
  static const String partial = 'partial';
}
