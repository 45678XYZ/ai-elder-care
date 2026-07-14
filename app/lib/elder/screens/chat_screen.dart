import 'package:flutter/material.dart';

/// 長者模式——語音對話畫面。
///
/// 免手持迴圈：裝置端 ASR 聆聽（zh-TW）→ POST /chat → 播放回覆音檔 → 自動再聆聽。
/// 回應 routines_updated 為 true 時重拉 GET /routines 並重排本地通知。
class ChatScreen extends StatelessWidget {
  const ChatScreen({super.key});

  @override
  Widget build(BuildContext context) => const Placeholder(); // TODO
}
