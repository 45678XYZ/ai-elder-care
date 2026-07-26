import 'package:flutter/material.dart';

import '../../theme/app_theme.dart';

/// 例行公事狀態的視覺定義。api.md 的 `status`：`pending` | `done` | `missed`。
///
/// 三者一律**顏色＋icon＋文字**同時呈現（MASTER.md §6：狀態不可只靠顏色，
/// 紅綠單色對色盲不友善）。
enum RoutineStatusStyle {
  done(
    label: '已完成',
    icon: Icons.check_circle,
    fg: AppColors.successFg,
    bg: AppColors.successBg,
  ),
  missed(
    label: '未完成',
    icon: Icons.error_outline,
    fg: AppColors.warnFg,
    bg: AppColors.warnBg,
  ),
  pending(
    label: '還沒到',
    icon: Icons.schedule,
    fg: AppColors.inkSecondary,
    bg: AppColors.chipSurface,
  );

  const RoutineStatusStyle({
    required this.label,
    required this.icon,
    required this.fg,
    required this.bg,
  });

  final String label;
  final IconData icon;
  final Color fg;
  final Color bg;

  /// 未知值一律當 pending（承 api.md 的寬容原則，不讓沒見過的字串弄壞畫面）。
  static RoutineStatusStyle from(String? status) => switch (status) {
        'done' => done,
        'missed' => missed,
        _ => pending,
      };
}

/// 例行公事狀態膠囊。
class RoutineStatusChip extends StatelessWidget {
  const RoutineStatusChip(this.status, {super.key, this.elderMode = false});

  final String status;
  final bool elderMode;

  @override
  Widget build(BuildContext context) {
    final s = RoutineStatusStyle.from(status);
    final text = Theme.of(context).textTheme;
    return Container(
      padding: EdgeInsets.symmetric(
        horizontal: elderMode ? 14 : 10,
        vertical: elderMode ? 8 : 5,
      ),
      decoration: BoxDecoration(
        color: s.bg,
        borderRadius: const BorderRadius.all(AppRadius.pill),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(s.icon, size: elderMode ? 26 : 16, color: s.fg),
          SizedBox(width: elderMode ? 8 : 5),
          Text(
            s.label,
            style: (elderMode ? text.headlineSmall : text.labelSmall)
                ?.copyWith(color: s.fg),
          ),
        ],
      ),
    );
  }
}

/// 事件分類膠囊（時間軸過濾、事件卡標籤）。
class EventTypeChip extends StatelessWidget {
  const EventTypeChip(
    this.category, {
    super.key,
    this.selected = true,
    this.onTap,
  });

  final EventCategory category;

  /// 當過濾器用時：未選中畫成描邊,選中畫成實心底。
  final bool selected;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final chip = Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
      decoration: BoxDecoration(
        color: selected ? category.bg : Colors.transparent,
        borderRadius: const BorderRadius.all(AppRadius.pill),
        border: Border.all(
          color: selected ? category.bg : AppColors.border,
          width: selected ? 2 : 1,
        ),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          // 選中時多一個勾，不讓「選了沒」只由底色承載（§6）
          if (onTap != null && selected) ...[
            Icon(Icons.check, size: 15, color: category.fg),
            const SizedBox(width: 4),
          ],
          Text(
            category.label,
            style: text.labelSmall?.copyWith(
              color: selected ? category.fg : AppColors.inkSecondary,
            ),
          ),
        ],
      ),
    );

    if (onTap == null) return chip;
    return Semantics(
      button: true,
      selected: selected,
      label: category.label,
      child: InkWell(
        onTap: onTap,
        borderRadius: const BorderRadius.all(AppRadius.pill),
        child: chip,
      ),
    );
  }
}

/// 時間軸圓點。飲食為方形、其餘圓形——分類不只靠顏色區分（app_theme 的 [EventCategory.dotShape]）。
class EventDot extends StatelessWidget {
  const EventDot(this.category, {super.key, this.size = 14});

  final EventCategory category;
  final double size;

  @override
  Widget build(BuildContext context) => Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          color: category.dot,
          shape: category.dotShape,
          borderRadius: category.dotShape == BoxShape.rectangle
              ? BorderRadius.circular(3)
              : null,
          // MASTER.md：圓點外環 0 0 0 1.5px <dot色>
          border: Border.all(color: category.bg, width: 2),
        ),
      );
}
