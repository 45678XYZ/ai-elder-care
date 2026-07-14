import 'package:flutter/material.dart';

void main() => runApp(const AiElderCareApp());

/// App 進入點。
///
/// 登入走 Cognito SDK（不經後端 API），登入後依帳號角色切換：
/// - 長者模式 → lib/elder/
/// - 照護者模式 → lib/caregiver/
class AiElderCareApp extends StatelessWidget {
  const AiElderCareApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '智慧長照陪伴',
      // TODO: 登入頁 → 依 Cognito 角色導向長者模式或照護者模式
      home: const Scaffold(body: Center(child: Text('智慧長照陪伴'))),
    );
  }
}
