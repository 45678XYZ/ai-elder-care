import 'package:flutter/material.dart';

import '../../shared/models/daily_summary.dart';
import '../../shared/models/api_page.dart';
import '../../shared/services/demo_data.dart';
import '../../shared/services/session_store.dart';
import '../../shared/widgets/app_card.dart';
import '../../shared/widgets/async_view.dart';
import '../../shared/widgets/care_header.dart';
import '../../shared/widgets/status_chip.dart';
import '../../theme/app_theme.dart';

/// S5 `/care/summary` — 照護者每日摘要。
///
/// `GET /summaries`：固定七類 sections，null 顯示「今日對話未提及」而不是留白——
/// 「沒提到」本身是資訊，跟「沒資料」不一樣。
///
/// 另外處理 api.md 的 hybrid 特性：摘要可能是 `partial`（當日還有對話沒整理完），
/// 這件事一定要讓照護者看見，否則會把半份摘要當成一整天的全貌。
class SummariesScreen extends StatefulWidget {
  const SummariesScreen({super.key});

  @override
  State<SummariesScreen> createState() => _SummariesScreenState();
}

class _SummariesScreenState extends State<SummariesScreen> {
  late Future<ApiPage<DailySummary>> _future;
  bool _generating = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  void _load() {
    _future = _fetch();
  }

  Future<ApiPage<DailySummary>> _fetch() async {
    await AppSession.instance.ensureEldersLoaded();
    // TODO: 後端上線後改為 api.getSummaries(elderId: AppSession.instance.selectedElderId!)
    return DemoData.summaries();
  }

  void _reload() => setState(_load);

  /// 手動生成（Demo 用）。§13：>300ms 要有可見 loading，成功要有明確回饋。
  Future<void> _generate() async {
    setState(() => _generating = true);
    try {
      // TODO: 後端上線後改為 api.generateSummary(elderId: ...)
      await Future<void>.delayed(DemoData.latency);
      if (!mounted) return;
      _reload();
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          backgroundColor: AppColors.barDark,
          content: Text('已重新產生今日摘要', style: TextStyle(color: AppColors.onDark)),
        ),
      );
    } finally {
      if (mounted) setState(() => _generating = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.app,
      body: SafeArea(
        child: Column(
          children: [
            CareHeader(
              title: '每日摘要',
              subtitle: '每天一份，內容取自長輩的對話',
              onElderChanged: (_) => _reload(),
              trailing: _GenerateButton(
                busy: _generating,
                onPressed: _generating ? null : _generate,
              ),
            ),
            Expanded(
              child: AsyncView<ApiPage<DailySummary>>(
                future: _future,
                onRetry: _reload,
                isEmpty: (p) => p.items.isEmpty,
                emptyIcon: Icons.wb_sunny_outlined,
                emptyText: '還沒有摘要\n長輩開始對話後，每天會自動整理一份',
                builder: (context, page) => ListView.builder(
                  padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
                  itemCount: page.items.length,
                  itemBuilder: (context, i) => Padding(
                    padding: const EdgeInsets.only(bottom: AppSpacing.lg),
                    child: _SummaryCard(
                      key: ValueKey(page.items[i].date),
                      summary: page.items[i],
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _GenerateButton extends StatelessWidget {
  const _GenerateButton({required this.busy, required this.onPressed});

  final bool busy;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 48, // >=48dp
      child: OutlinedButton.icon(
        onPressed: onPressed,
        icon: busy
            ? const SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(
                    strokeWidth: 2, color: AppColors.accent),
              )
            : const Icon(Icons.refresh, size: 18),
        label: Text(busy ? '產生中' : '重新產生',
            style: Theme.of(context).textTheme.labelSmall),
        style: OutlinedButton.styleFrom(
          foregroundColor: AppColors.ink,
          side: const BorderSide(color: AppColors.border),
          shape: const RoundedRectangleBorder(
            borderRadius: BorderRadius.all(AppRadius.field),
          ),
        ),
      ),
    );
  }
}

/// 單日摘要卡：日期塊 → 完整度 → 總覽 → 警訊 → 七類 → 例行公事。
class _SummaryCard extends StatelessWidget {
  const _SummaryCard({super.key, required this.summary});

  final DailySummary summary;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;

    return AppCard(
      padding: const EdgeInsets.all(AppSpacing.lg),
      radius: AppRadius.cardLarge,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Wrap 而非 Row：字級放大到兩倍時日期塊與次數並排會超出卡片寬度，
          // 換行讓兩者各佔一行，不必犧牲任何一邊的內容（不做 ellipsis）。
          SizedBox(
            width: double.infinity,
            child: Wrap(
              alignment: WrapAlignment.spaceBetween,
              crossAxisAlignment: WrapCrossAlignment.center,
              spacing: AppSpacing.sm,
              runSpacing: AppSpacing.sm,
              children: [
                // 摘要日期塊：底 #f3ecdd、圓角 12（design-system/pages/caregiver-mode.md）
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
                  decoration: const BoxDecoration(
                    color: AppColors.app,
                    borderRadius: BorderRadius.all(AppRadius.field),
                  ),
                  child:
                      Text(_dateLabel(summary.date), style: text.labelMedium),
                ),
                Text('${summary.interactionCount} 次對話',
                    style: text.bodySmall
                        ?.copyWith(color: AppColors.inkSecondary)),
              ],
            ),
          ),

          // partial：hybrid 架構下這份摘要還沒涵蓋整天，必須明說
          if (summary.isPartial) ...[
            const SizedBox(height: AppSpacing.md),
            _PartialNotice(pending: summary.pendingSessionCount),
          ],

          if (summary.overview != null) ...[
            const SizedBox(height: AppSpacing.md),
            Text(summary.overview!, style: text.bodyLarge),
          ],

          if (summary.alerts.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.md),
            for (final a in summary.alerts)
              Padding(
                padding: const EdgeInsets.only(bottom: AppSpacing.xs),
                child: _AlertRow(a),
              ),
          ],

          const SizedBox(height: AppSpacing.lg),
          const SectionHeader('生活紀錄'),
          const SizedBox(height: AppSpacing.sm),
          AppCard.nested(
            child: Column(
              children: [
                for (final e in _sectionEntries(summary.sections))
                  _SectionRow(category: e.$1, content: e.$2),
              ],
            ),
          ),

          if (summary.routines.items.isNotEmpty) ...[
            const SizedBox(height: AppSpacing.lg),
            SectionHeader(
              '例行公事',
              trailing: Text(
                '完成 ${summary.routines.completed}・未完成 ${summary.routines.missed}',
                style: text.bodySmall?.copyWith(color: AppColors.inkSecondary),
              ),
            ),
            const SizedBox(height: AppSpacing.sm),
            Wrap(
              spacing: AppSpacing.sm,
              runSpacing: AppSpacing.sm,
              children: [
                for (final r in summary.routines.items)
                  _RoutinePill(title: r.title, status: r.status),
              ],
            ),
          ],
        ],
      ),
    );
  }

  /// 七類固定全列，null 也要出現——「今日對話未提及」本身就是給照護者的資訊。
  /// 順序照 api.md 的 `EventType`。
  List<(EventCategory, String?)> _sectionEntries(SummarySections s) => [
        (EventCategory.diet, s.diet),
        (EventCategory.activity, s.activity),
        (EventCategory.sleep, s.sleep),
        (EventCategory.medication, s.medication),
        (EventCategory.wellbeing, s.wellbeing),
        (EventCategory.safety, s.safety),
        (EventCategory.other, s.other),
      ];

  static String _dateLabel(String iso) {
    final parts = iso.split('-');
    if (parts.length != 3) return iso;
    return '${int.tryParse(parts[1]) ?? parts[1]} 月 ${int.tryParse(parts[2]) ?? parts[2]} 日';
  }
}

/// 摘要不完整的提示。用 icon＋文字，不只靠底色（§6）。
class _PartialNotice extends StatelessWidget {
  const _PartialNotice({required this.pending});

  final int pending;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: const BoxDecoration(
        color: AppColors.warnBg,
        borderRadius: BorderRadius.all(AppRadius.field),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.hourglass_bottom, size: 18, color: AppColors.warnFg),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Text(
              pending > 0
                  ? '還有 $pending 段對話正在整理，這份摘要尚未涵蓋今天全部內容。'
                  : '這份摘要尚未涵蓋今天全部內容。',
              style: text.bodySmall?.copyWith(color: AppColors.warnFg),
            ),
          ),
        ],
      ),
    );
  }
}

class _AlertRow extends StatelessWidget {
  const _AlertRow(this.message);

  final String message;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Icon(Icons.priority_high, size: 18, color: AppColors.warnFg),
        const SizedBox(width: AppSpacing.sm),
        Expanded(
          child: Text(message,
              style: text.bodyMedium?.copyWith(color: AppColors.warnFg)),
        ),
      ],
    );
  }
}

class _SectionRow extends StatelessWidget {
  const _SectionRow({required this.category, required this.content});

  final EventCategory category;
  final String? content;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final empty = content == null || content!.trim().isEmpty;

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 固定寬度標籤欄，讓七類的內容左緣對齊；用 Wrap 之外的最小可預測排版
          SizedBox(
            width: 56,
            child: Text(category.label,
                style: text.labelSmall?.copyWith(color: category.fg)),
          ),
          const SizedBox(width: AppSpacing.sm),
          Expanded(
            child: Text(
              empty ? '今日對話未提及' : content!,
              style: text.bodyMedium?.copyWith(
                color: empty ? AppColors.chevron : AppColors.ink,
                fontStyle: empty ? FontStyle.italic : null,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _RoutinePill extends StatelessWidget {
  const _RoutinePill({required this.title, required this.status});

  final String title;
  final String status;

  @override
  Widget build(BuildContext context) {
    final s = RoutineStatusStyle.from(status);
    final text = Theme.of(context).textTheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: s.bg,
        borderRadius: const BorderRadius.all(AppRadius.pill),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(s.icon, size: 15, color: s.fg),
          const SizedBox(width: 5),
          Text(title, style: text.bodySmall?.copyWith(color: s.fg)),
        ],
      ),
    );
  }
}
