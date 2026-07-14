import 'package:flutter/material.dart';

/// 照護者模式——每日摘要列表。
///
/// GET /summaries（固定六類 sections，null 顯示「今日對話未提及」）；
/// 另提供手動觸發 POST /summaries/generate。
class SummariesScreen extends StatelessWidget {
  const SummariesScreen({super.key});

  @override
  Widget build(BuildContext context) => const Placeholder(); // TODO
}
