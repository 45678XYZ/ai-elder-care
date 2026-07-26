import 'package:flutter/material.dart';

import 'app_router.dart';
import 'shared/services/session_store.dart';
import 'theme/app_theme.dart';

Future<void> main() async {
  // 讀取持久化設定前需先初始化 binding。
  WidgetsFlutterBinding.ensureInitialized();
  await AppSession.instance.load();
  runApp(AiElderCareApp());
}

/// App 進入點。
///
/// 登入走 Cognito SDK（不經後端 API），登入後依帳號角色切換：
/// - 長者模式 → lib/elder/
/// - 照護者模式 → lib/caregiver/
///
/// 落點：首次安裝進 S1 首次設定，之後啟動直接進 S2 角色選擇（見 session_store）。
/// 正式流程由登入角色決定。
class AiElderCareApp extends StatelessWidget {
  AiElderCareApp({super.key});

  final _router = buildRouter(
    initialLocation: AppSession.instance.setupDone ? '/' : '/setup',
  );

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: '智慧長照陪伴',
      debugShowCheckedModeBanner: false,
      theme: buildAppTheme(),
      routerConfig: _router,
    );
  }
}
