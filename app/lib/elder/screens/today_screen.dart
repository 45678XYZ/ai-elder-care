import 'package:flutter/material.dart';

/// 長者模式——今日行程顯示與提醒。
///
/// 資料來源 GET /routines?elder_id=&date=（當日行程視圖）；
/// 可手動確認完成（POST /routines/{id}/complete）。
class TodayScreen extends StatelessWidget {
  const TodayScreen({super.key});

  @override
  Widget build(BuildContext context) => const Placeholder(); // TODO
}
