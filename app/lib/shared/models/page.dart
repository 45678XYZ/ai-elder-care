/// 列表端點的一頁結果。分頁規則見 docs/api.md「共通慣例」。
///
/// [nextToken] 是後端編碼的**不透明游標字串**，前端不得解析其內容，只能原樣帶回
/// 下一次請求的 `next_token`；為 null（回應中沒有該欄位）即表示已無更多資料。
class Page<T> {
  const Page({
    required this.items,
    this.nextToken,
  });

  final List<T> items;
  final String? nextToken;

  /// 還有下一頁可取。
  bool get hasMore => nextToken != null && nextToken!.isNotEmpty;

  /// 從 `{items: [...], next_token?: "..."}` 解析，每個 item 交給 [fromJson]。
  factory Page.fromJson(
    Map<String, dynamic> json,
    T Function(Map<String, dynamic>) fromJson,
  ) {
    final raw = json['items'] as List<dynamic>? ?? const [];
    final token = json['next_token'] as String?;
    return Page(
      items: raw.map((e) => fromJson(e as Map<String, dynamic>)).toList(),
      nextToken: (token == null || token.isEmpty) ? null : token,
    );
  }
}
