import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../theme/app_theme.dart';

/// 長者模式外殼：底部 2 tab（聊天／今日）。
/// 觸控 >=60dp、字級 >=24sp、狀態同時靠顏色＋icon＋文字（§5.4／§9）。
/// 不用 Material NavigationBar：其標籤字級固定偏小，違反長者 24sp 下限。
class ElderShell extends StatelessWidget {
  const ElderShell({super.key, required this.navigationShell});

  final StatefulNavigationShell navigationShell;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: navigationShell,
      bottomNavigationBar: Container(
        decoration: const BoxDecoration(
          color: AppColors.card,
          boxShadow: AppShadows.voicePanel,
        ),
        child: SafeArea(
          top: false,
          child: Row(
            children: [
              _ElderTab(
                icon: Icons.chat_bubble_outline,
                selectedIcon: Icons.chat_bubble,
                label: '聊天',
                selected: navigationShell.currentIndex == 0,
                onTap: () => navigationShell.goBranch(0),
              ),
              _ElderTab(
                icon: Icons.today_outlined,
                selectedIcon: Icons.today,
                label: '今日',
                selected: navigationShell.currentIndex == 1,
                onTap: () => navigationShell.goBranch(1),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ElderTab extends StatelessWidget {
  const _ElderTab({
    required this.icon,
    required this.selectedIcon,
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final IconData icon;
  final IconData selectedIcon;
  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = selected ? AppColors.accentText : AppColors.inkSecondary;
    return Expanded(
      child: Semantics(
        selected: selected,
        button: true,
        label: label,
        child: InkWell(
          onTap: onTap,
          child: Container(
            constraints: const BoxConstraints(minHeight: 72), // >=60dp 觸控
            padding: const EdgeInsets.symmetric(vertical: 10),
            decoration: BoxDecoration(
              color: selected ? AppColors.chipSurface : Colors.transparent,
              border: Border(
                top: BorderSide(
                  color: selected ? AppColors.accent : Colors.transparent,
                  width: 3,
                ),
              ),
            ),
            child: Column(
              // min：底部列高度限制是鬆的（可到滿版），不設 min 會讓 tab 撐滿整個
              // 畫面、把 body（聊天畫面）壓成 0 高度。
              mainAxisSize: MainAxisSize.min,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(selected ? selectedIcon : icon, size: 34, color: color),
                const SizedBox(height: 2),
                Text(
                  label,
                  style: TextStyle(
                    fontSize: 24, // 長者字級下限
                    fontWeight: FontWeight.w700,
                    color: color,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
