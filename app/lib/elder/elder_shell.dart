import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../shared/i18n/strings.dart';
import '../shared/services/session_store.dart';
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
          color: AppColors.cardAlt,
          border: Border(
            top: BorderSide(color: AppColors.border, width: 1),
          ),
          boxShadow: AppShadows.voicePanel,
        ),
        child: SafeArea(
          top: false,
          child: Padding(
            padding: const EdgeInsets.symmetric(
                horizontal: AppSpacing.sm, vertical: AppSpacing.sm),
            // 分頁列自己聽 textLangRevision：長輩在今日頁換了書寫語言，這一列在
            // 畫面外側、不屬於任何一頁，不主動聽就會一直停在切換前那兩個字。
            child: ValueListenableBuilder<int>(
              valueListenable: AppSession.textLangRevision,
              builder: (context, _, __) => Row(
                children: [
                  _ElderTab(
                    icon: Icons.chat_bubble_outline,
                    selectedIcon: Icons.chat_bubble,
                    label: t('聊天'),
                    selected: navigationShell.currentIndex == 0,
                    onTap: () => navigationShell.goBranch(0),
                  ),
                  _ElderTab(
                    icon: Icons.event_note_outlined,
                    selectedIcon: Icons.event_note,
                    label: t('今日行程'),
                    selected: navigationShell.currentIndex == 1,
                    onTap: () => navigationShell.goBranch(1),
                  ),
                ],
              ),
            ),
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
    final text = Theme.of(context).textTheme;
    final color = selected ? Colors.white : AppColors.inkSecondary;
    return Expanded(
      child: Semantics(
        selected: selected,
        button: true,
        label: label,
        child: InkWell(
          onTap: onTap,
          borderRadius: const BorderRadius.all(AppRadius.card),
          child: Container(
            constraints: const BoxConstraints(minHeight: 68), // >=60dp 觸控
            padding: const EdgeInsets.symmetric(
                horizontal: AppSpacing.sm, vertical: AppSpacing.md),
            // 選中的那一格整塊上朱紅、白字白 icon：對長輩來說「我現在在哪裡」
            // 要一眼看得出來，細線或淡底色的差異在老花與強光下都容易失守。
            // 形狀（實心／空心 icon）另外承載一次，不只靠顏色（§9）。
            decoration: BoxDecoration(
              color: selected ? AppColors.accentText : Colors.transparent,
              borderRadius: const BorderRadius.all(AppRadius.card),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(selected ? selectedIcon : icon, size: 30, color: color),
                const SizedBox(width: AppSpacing.sm),
                // Flexible：兩倍字級時「今日行程」會換行而不是撐破格子。
                Flexible(
                  child: Text(
                    label,
                    textAlign: TextAlign.center,
                    style: text.headlineSmall?.copyWith(color: color),
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
