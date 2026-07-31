/// RAG PoC `POST /ask` 端點的回應（`{answer, sources}`）。
///
/// 這是過渡用的簡化契約；正式後端上線後由 [ChatReply]（`POST /chat`，見 docs/api.md）取代。
class AskResult {
  const AskResult({required this.answer, required this.sources});

  final String answer;
  final List<KbSource> sources;

  factory AskResult.fromJson(Map<String, dynamic> json) => AskResult(
        answer: json['answer'] as String? ?? '',
        sources: (json['sources'] as List<dynamic>? ?? const [])
            .map((e) => KbSource.fromJson(e as Map<String, dynamic>))
            .toList(),
      );
}

/// 一筆知識庫引用來源。
class KbSource {
  const KbSource({required this.title, required this.url});

  final String title;
  final String url;

  factory KbSource.fromJson(Map<String, dynamic> json) => KbSource(
        title: json['title'] as String? ?? '',
        url: json['url'] as String? ?? '',
      );
}
