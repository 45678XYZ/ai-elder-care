import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../theme/app_theme.dart';

/// 照護者模式外殼：底部 4 tab（摘要／時間軸／統計／管理）。
/// 觸控 >=48dp（NavigationBar 預設高度即符合）。tab 數 <=5（§6）。
class CaregiverShell extends StatelessWidget {
  const CaregiverShell({super.key, required this.navigationShell});

  /// 由 StatefulShellRoute.indexedStack 注入，保留各分支的畫面狀態與捲動位置。
  final StatefulNavigationShell navigationShell;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: navigationShell,
      bottomNavigationBar: NavigationBar(
        backgroundColor: AppColors.card,
        indicatorColor: AppColors.chipSurface,
        surfaceTintColor: Colors.transparent,
        selectedIndex: navigationShell.currentIndex,
        // goBranch：切 tab 不重建、保留狀態；再次點同 tab 回到該分支根。
        onDestinationSelected: (i) => navigationShell.goBranch(i,
            initialLocation: i == navigationShell.currentIndex),
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.wb_sunny_outlined, color: AppColors.inkSecondary),
            selectedIcon: Icon(Icons.wb_sunny, color: AppColors.accentText),
            label: '摘要',
          ),
          NavigationDestination(
            icon: Icon(Icons.timeline_outlined, color: AppColors.inkSecondary),
            selectedIcon: Icon(Icons.timeline, color: AppColors.accentText),
            label: '時間軸',
          ),
          NavigationDestination(
            icon: Icon(Icons.bar_chart_outlined, color: AppColors.inkSecondary),
            selectedIcon: Icon(Icons.bar_chart, color: AppColors.accentText),
            label: '統計',
          ),
          NavigationDestination(
            icon: Icon(Icons.tune_outlined, color: AppColors.inkSecondary),
            selectedIcon: Icon(Icons.tune, color: AppColors.accentText),
            label: '管理',
          ),
        ],
      ),
    );
  }
}
