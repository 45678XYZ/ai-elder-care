import 'package:flutter/material.dart';

import '../../theme/app_theme.dart';
import '../models/elder.dart';
import '../services/session_store.dart';
import 'app_card.dart';

/// 照護者四個畫面共用的頁首：畫面標題＋目前在看哪位長輩。
///
/// 存在的理由是 api.md 的一個結構事實——`/summaries`、`/events`、`/routines`、`/stats`
/// 每一個都要 `elder_id`，所以「現在看的是誰」必須隨時可見、可換，否則照護者會看錯人的資料。
/// 綁多位長輩時才顯示切換入口（單人時只顯示名字，不製造沒有用的按鈕）。
class CareHeader extends StatefulWidget {
  const CareHeader({
    super.key,
    required this.title,
    this.subtitle,
    this.onElderChanged,
    this.trailing,
  });

  final String title;
  final String? subtitle;

  /// 切換長輩後通知畫面重拉資料；不給就不顯示切換入口。
  final ValueChanged<Elder>? onElderChanged;

  final Widget? trailing;

  @override
  State<CareHeader> createState() => _CareHeaderState();
}

class _CareHeaderState extends State<CareHeader> {
  @override
  void initState() {
    super.initState();
    _ensureElders();
  }

  /// 自己確保長輩清單已載入並重畫。
  ///
  /// 畫面各自 await 過 [AppSession.ensureEldersLoaded]，但那是在 FutureBuilder 裡，
  /// 完成時不會重建外層的 header——第一次進照護者模式就會卡在「尚未選擇長輩」。
  Future<void> _ensureElders() async {
    if (AppSession.instance.elders.isNotEmpty) return;
    await AppSession.instance.ensureEldersLoaded();
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final session = AppSession.instance;
    final elder = session.selectedElder;
    final canSwitch =
        widget.onElderChanged != null && session.elders.length > 1;

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(child: Text(widget.title, style: text.titleLarge)),
              if (widget.trailing != null) widget.trailing!,
            ],
          ),
          if (widget.subtitle != null) ...[
            const SizedBox(height: 2),
            Text(widget.subtitle!,
                style: text.bodySmall?.copyWith(color: AppColors.inkSecondary)),
          ],
          const SizedBox(height: AppSpacing.md),
          _ElderBar(
            elder: elder,
            canSwitch: canSwitch,
            onTap: canSwitch ? () => _pickElder(context) : null,
          ),
        ],
      ),
    );
  }

  Future<void> _pickElder(BuildContext context) async {
    final session = AppSession.instance;
    final picked = await showModalBottomSheet<Elder>(
      context: context,
      backgroundColor: AppColors.cardAlt,
      shape: const RoundedRectangleBorder(borderRadius: AppRadius.voicePanel),
      builder: (sheetContext) => _ElderPicker(
        elders: session.elders,
        selectedId: session.selectedElderId,
      ),
    );
    if (picked == null) return;
    await session.selectElder(picked.elderId);
    if (mounted) setState(() {});
    widget.onElderChanged?.call(picked);
  }
}

/// 目前長輩列。可切換時右側給雪佛龍，不可切換時純顯示。
class _ElderBar extends StatelessWidget {
  const _ElderBar({
    required this.elder,
    required this.canSwitch,
    required this.onTap,
  });

  final Elder? elder;
  final bool canSwitch;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final name = elder?.nickname?.trim().isNotEmpty == true
        ? elder!.nickname!.trim()
        : (elder?.name ?? '尚未選擇長輩');

    return AppCard(
      color: AppColors.chipSurface,
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      radius: AppRadius.field,
      shadows: const [],
      onTap: onTap,
      semanticLabel: canSwitch ? '目前長輩 $name，點一下切換' : '目前長輩 $name',
      child: Row(
        children: [
          const Icon(Icons.person_outline, size: 20, color: AppColors.avatarFg),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Text(
              canSwitch ? '正在看　$name' : name,
              style: text.labelMedium,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (canSwitch) ...[
            Text('切換',
                style: text.bodySmall?.copyWith(color: AppColors.accentText)),
            const Icon(Icons.expand_more, size: 20, color: AppColors.chevron),
          ],
        ],
      ),
    );
  }
}

/// 長輩切換面板。有明確關閉鈕，不只靠往下滑（MASTER.md §12 modal escape）。
class _ElderPicker extends StatelessWidget {
  const _ElderPicker({required this.elders, required this.selectedId});

  final List<Elder> elders;
  final String? selectedId;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(child: Text('切換長輩', style: text.titleMedium)),
                IconButton(
                  onPressed: () => Navigator.of(context).pop(),
                  tooltip: '關閉',
                  icon: const Icon(Icons.close, color: AppColors.ink),
                ),
              ],
            ),
            const SizedBox(height: AppSpacing.sm),
            for (final e in elders)
              Padding(
                padding: const EdgeInsets.only(bottom: AppSpacing.sm),
                child: _ElderOption(
                  elder: e,
                  selected: e.elderId == selectedId,
                  onTap: () => Navigator.of(context).pop(e),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _ElderOption extends StatelessWidget {
  const _ElderOption({
    required this.elder,
    required this.selected,
    required this.onTap,
  });

  final Elder elder;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final sub = [
      if (elder.birthYear != null)
        '${DateTime.now().year - elder.birthYear!} 歲',
      if (elder.addressRegion != null) elder.addressRegion!,
    ].join('・');

    return AppCard(
      padding: const EdgeInsets.all(AppSpacing.md),
      border: Border.all(
        color: selected ? AppColors.accentText : AppColors.borderInteractive,
        width: selected ? 2 : 1,
      ),
      onTap: onTap,
      semanticLabel: '${elder.name}${selected ? '，目前選定' : ''}',
      child: Row(
        children: [
          // 選中同時用勾與外框表示，不只靠顏色
          Container(
            width: 22,
            height: 22,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: selected ? AppColors.accentText : Colors.transparent,
              border: Border.all(
                color: selected ? AppColors.accentText : AppColors.chevron,
                width: 2,
              ),
            ),
            child: selected
                ? const Icon(Icons.check, size: 14, color: Colors.white)
                : null,
          ),
          const SizedBox(width: AppSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                    elder.nickname?.trim().isNotEmpty == true
                        ? '${elder.nickname}（${elder.name}）'
                        : elder.name,
                    style: text.titleSmall),
                if (sub.isNotEmpty)
                  Text(sub,
                      style: text.bodySmall
                          ?.copyWith(color: AppColors.inkSecondary)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
