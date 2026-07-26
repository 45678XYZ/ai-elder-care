import 'package:flutter/material.dart';

import '../../theme/app_theme.dart';

/// 紙感卡片——暖紙手帳的基本容器。
///
/// 只負責底色、圓角、陰影與內距；字級由使用端依模式決定（長者 >=24sp、照護者 13–22sp），
/// 所以兩個模式共用同一個 widget，不各寫一份（CLAUDE.md 全域約束）。
class AppCard extends StatelessWidget {
  const AppCard({
    super.key,
    required this.child,
    this.color = AppColors.card,
    this.padding = const EdgeInsets.all(AppSpacing.lg),
    this.radius = AppRadius.card,
    this.border,
    this.shadows = AppShadows.card,
    this.onTap,
    this.semanticLabel,
  });

  /// 巢狀卡片（例行公事／摘要區塊內）用 [AppColors.nest]。
  const AppCard.nested({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(AppSpacing.md),
    this.radius = AppRadius.field,
    this.border,
    this.onTap,
    this.semanticLabel,
  })  : color = AppColors.nest,
        shadows = const <BoxShadow>[];

  final Widget child;
  final Color color;
  final EdgeInsets padding;
  final Radius radius;
  final BoxBorder? border;
  final List<BoxShadow> shadows;
  final VoidCallback? onTap;
  final String? semanticLabel;

  @override
  Widget build(BuildContext context) {
    final box = Container(
      width: double.infinity,
      padding: padding,
      decoration: BoxDecoration(
        color: color,
        borderRadius: BorderRadius.all(radius),
        border: border,
        boxShadow: shadows,
      ),
      child: child,
    );

    if (onTap == null) {
      return semanticLabel == null
          ? box
          : Semantics(label: semanticLabel, child: box);
    }
    return Semantics(
      button: true,
      label: semanticLabel,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.all(radius),
        child: box,
      ),
    );
  }
}

/// 分區標題。字距 .12em、灰咖色——農民曆牌面的分區感（MASTER.md §字距）。
class SectionHeader extends StatelessWidget {
  const SectionHeader(this.title,
      {super.key, this.trailing, this.elderMode = false});

  final String title;
  final Widget? trailing;
  final bool elderMode;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Row(
      children: [
        Expanded(
          child: Text(
            title,
            style:
                (elderMode ? text.headlineSmall : text.labelMedium)?.copyWith(
              color: elderMode ? AppColors.ink : AppColors.inkSecondary,
              letterSpacing: elderMode ? 0 : 1.7,
            ),
          ),
        ),
        if (trailing != null) trailing!,
      ],
    );
  }
}

/// 朱紅分隔線——農民曆牌面用（today_screen 頂部）。
class AccentDivider extends StatelessWidget {
  const AccentDivider({super.key, this.width = 64, this.thickness = 3});

  final double width;
  final double thickness;

  @override
  Widget build(BuildContext context) => Container(
        width: width,
        height: thickness,
        decoration: BoxDecoration(
          color: AppColors.accent,
          borderRadius: BorderRadius.circular(thickness),
        ),
      );
}
