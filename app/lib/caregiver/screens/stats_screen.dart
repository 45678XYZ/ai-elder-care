import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';

import '../../shared/models/stats.dart';
import '../../shared/services/care_repository.dart';
import '../../shared/services/session_store.dart';
import '../../shared/widgets/app_card.dart';
import '../../shared/widgets/async_view.dart';
import '../../shared/widgets/auto_refresh.dart';
import '../../shared/widgets/care_header.dart';
import '../../theme/app_theme.dart';

/// S7 `/care/stats` — 互動與例行公事統計。
///
/// `GET /stats`：今日互動、期間互動與活躍天數、例行公事逐項完成、daily 趨勢。
///
/// 圖表選型照 MASTER.md §14：趨勢用折線、比較類別用長條（由大到小、每條標數值）、
/// **不用圓餅圖**（依賴顏色辨識，對色盲不友善）。所有圖都附數值標籤，
/// 另可切換成資料表——圖表不是唯一的資訊來源。
class StatsScreen extends StatefulWidget {
  const StatsScreen({super.key});

  @override
  State<StatsScreen> createState() => _StatsScreenState();
}

class _StatsScreenState extends State<StatsScreen>
    with AutoRefreshState<StatsScreen> {
  late Future<Stats> _future;
  bool _showTable = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  /// 今日互動次數與最後互動時間會隨著長輩講話一路變動，這一頁卻是照護者最常
  /// 開著不動的一頁（「他今天講過話了嗎」）。載入失敗一律吞掉，維持上一份。
  @override
  Future<void> autoRefresh() async {
    try {
      final stats = await _fetch();
      if (!mounted) return;
      setState(() => _future = Future.value(stats));
    } catch (_) {
      // 靜默：畫面維持上一份成功的資料
    }
  }

  void _load() {
    _future = _fetch();
  }

  Future<Stats> _fetch() async {
    await AppSession.instance.ensureEldersLoaded();
    final elderId = AppSession.instance.selectedElderId;
    // 還沒綁定任何長輩——剛註冊的照護者必然是這個狀態。這裡原本是 `selectedElderId!`，
    // null 時當場丟 Null check operator，整頁變成「載入失敗」加一顆重試鈕，
    // 而重試永遠不會成功。「還沒有長輩」是正常狀態不是錯誤，回一份空統計讓畫面照常畫。
    // 摘要、時間軸、管理頁都踩過同一個坑。
    if (elderId == null) {
      return const Stats(
        elderId: '',
        today: StatsToday(),
        period: StatsPeriod(),
      );
    }
    return CareRepo.instance.stats(elderId: elderId, days: 7);
  }

  void _reload() => setState(_load);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.app,
      body: SafeArea(
        child: Column(
          children: [
            CareHeader(
              title: '統計',
              subtitle: '互動頻率與例行公事完成情況',
              onElderChanged: (_) => _reload(),
            ),
            Expanded(
              child: AsyncView<Stats>(
                future: _future,
                onRetry: _reload,
                builder: (context, stats) => ListView(
                  padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
                  children: [
                    _TodayCard(stats: stats),
                    const SizedBox(height: AppSpacing.lg),
                    _PeriodCard(period: stats.period),
                    const SizedBox(height: AppSpacing.lg),
                    _RoutineCompletionCard(items: stats.byRoutine),
                    const SizedBox(height: AppSpacing.lg),
                    _TrendCard(
                      daily: stats.daily,
                      showTable: _showTable,
                      onToggleTable: () =>
                          setState(() => _showTable = !_showTable),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 今日：互動次數 + 最後互動時間。
class _TodayCard extends StatelessWidget {
  const _TodayCard({required this.stats});

  final Stats stats;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final last = stats.today.lastInteractionAt;

    return AppCard(
      radius: AppRadius.cardLarge,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionHeader('今天'),
          const SizedBox(height: AppSpacing.md),
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text('${stats.today.interactionCount}',
                  style: text.displayMedium),
              const SizedBox(width: AppSpacing.sm),
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Text('次對話',
                    style: text.bodyLarge
                        ?.copyWith(color: AppColors.inkSecondary)),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              const Icon(Icons.schedule, size: 16, color: AppColors.chevron),
              const SizedBox(width: AppSpacing.sm),
              Expanded(
                child: Text(
                  last == null ? '今天還沒有對話' : '最後一次互動：${_relative(last)}',
                  style:
                      text.bodySmall?.copyWith(color: AppColors.inkSecondary),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  static String _relative(DateTime t) {
    final diff = DateTime.now().difference(t);
    if (diff.inMinutes < 1) return '剛剛';
    if (diff.inMinutes < 60) return '${diff.inMinutes} 分鐘前';
    if (diff.inHours < 24) return '${diff.inHours} 小時前';
    return '${t.month} 月 ${t.day} 日';
  }
}

/// 期間彙總：兩個數字卡並排。
class _PeriodCard extends StatelessWidget {
  const _PeriodCard({required this.period});

  final StatsPeriod period;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: _StatTile(
            value: '${period.interactionCount}',
            unit: '次',
            label: '近 ${period.days} 天對話',
          ),
        ),
        const SizedBox(width: AppSpacing.md),
        Expanded(
          child: _StatTile(
            value: '${period.activeDays}',
            unit: '/ ${period.days} 天',
            label: '有互動的日子',
          ),
        ),
      ],
    );
  }
}

class _StatTile extends StatelessWidget {
  const _StatTile({
    required this.value,
    required this.unit,
    required this.label,
  });

  final String value;
  final String unit;
  final String label;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return AppCard(
      padding: const EdgeInsets.all(AppSpacing.md),
      semanticLabel: '$label $value $unit',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label,
              style: text.bodySmall?.copyWith(color: AppColors.inkSecondary)),
          const SizedBox(height: AppSpacing.sm),
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Flexible(
                child: Text(value,
                    style: text.titleLarge?.copyWith(fontSize: 30),
                    overflow: TextOverflow.ellipsis),
              ),
              const SizedBox(width: 4),
              Padding(
                padding: const EdgeInsets.only(bottom: 3),
                child: Text(unit,
                    style: text.bodySmall
                        ?.copyWith(color: AppColors.inkSecondary)),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// 例行公事逐項完成——長條圖，由大到小排序，每條標數值（§14）。
///
/// 自己畫而不用圖表庫：條在卡片寬度內用比例撐開，數值與百分比一律顯示，
/// textScaler 放大時仍然只是換行，不會像 canvas 圖表那樣被裁掉。
class _RoutineCompletionCard extends StatelessWidget {
  const _RoutineCompletionCard({required this.items});

  final List<RoutineStat> items;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    if (items.isEmpty) {
      return AppCard(
        radius: AppRadius.cardLarge,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionHeader('例行公事完成率'),
            const SizedBox(height: AppSpacing.md),
            Text('這段期間沒有排定的例行公事',
                style:
                    text.bodyMedium?.copyWith(color: AppColors.inkSecondary)),
          ],
        ),
      );
    }

    // 由大到小（完成率相同時比完成數），§14 要求排序
    final sorted = items.toList()
      ..sort((a, b) {
        final ra = a.total == 0 ? 0.0 : a.completed / a.total;
        final rb = b.total == 0 ? 0.0 : b.completed / b.total;
        final c = rb.compareTo(ra);
        return c != 0 ? c : b.completed.compareTo(a.completed);
      });

    return AppCard(
      radius: AppRadius.cardLarge,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionHeader('例行公事完成率'),
          const SizedBox(height: AppSpacing.lg),
          for (final s in sorted) ...[
            _RoutineBar(stat: s),
            const SizedBox(height: AppSpacing.md),
          ],
        ],
      ),
    );
  }
}

class _RoutineBar extends StatelessWidget {
  const _RoutineBar({required this.stat});

  final RoutineStat stat;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final ratio = stat.total == 0 ? 0.0 : stat.completed / stat.total;
    final percent = (ratio * 100).round();
    // 低於七成用警示色——但數值本來就恆顯示，顏色只是輔助（§6）
    final barColor = ratio >= 0.7 ? AppColors.accent : AppColors.warnFg;

    return Semantics(
      label: '${stat.title}，完成 ${stat.completed} 次，共 ${stat.total} 次，$percent%',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(child: Text(stat.title, style: text.bodyMedium)),
              const SizedBox(width: AppSpacing.sm),
              Text('${stat.completed} / ${stat.total}',
                  style: text.labelSmall?.copyWith(color: AppColors.ink)),
              const SizedBox(width: AppSpacing.sm),
              Text('$percent%',
                  style: text.labelSmall?.copyWith(color: barColor)),
            ],
          ),
          const SizedBox(height: 6),
          ClipRRect(
            borderRadius: BorderRadius.circular(5),
            child: Stack(
              children: [
                Container(height: 10, color: AppColors.track),
                FractionallySizedBox(
                  widthFactor: ratio.clamp(0.0, 1.0),
                  child: Container(height: 10, color: barColor),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// 每日互動趨勢——折線圖；資料點 <4 時改用數字列（§14）。
class _TrendCard extends StatelessWidget {
  const _TrendCard({
    required this.daily,
    required this.showTable,
    required this.onToggleTable,
  });

  final List<DailyStat> daily;
  final bool showTable;
  final VoidCallback onToggleTable;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;

    return AppCard(
      radius: AppRadius.cardLarge,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SectionHeader(
            '每日對話次數',
            trailing: TextButton(
              onPressed: onToggleTable,
              style: TextButton.styleFrom(
                minimumSize: const Size(48, 48),
                foregroundColor: AppColors.accentText,
              ),
              child: Text(showTable ? '看圖表' : '看數字',
                  style:
                      text.labelSmall?.copyWith(color: AppColors.accentText)),
            ),
          ),
          const SizedBox(height: AppSpacing.md),
          if (daily.length < 4)
            // 資料點太少畫折線沒有意義，直接列數字
            _DailyNumbers(daily: daily)
          else if (showTable)
            _DailyNumbers(daily: daily)
          else
            SizedBox(height: 180, child: _LineChart(daily: daily)),
        ],
      ),
    );
  }
}

class _LineChart extends StatelessWidget {
  const _LineChart({required this.daily});

  final List<DailyStat> daily;

  @override
  Widget build(BuildContext context) {
    final maxY = daily
        .map((d) => d.interactionCount)
        .fold<int>(0, (a, b) => a > b ? a : b)
        .toDouble();

    return LineChart(
      LineChartData(
        minY: 0,
        maxY: (maxY + 2).clamp(4, double.infinity),
        gridData: FlGridData(
          show: true,
          drawVerticalLine: false,
          getDrawingHorizontalLine: (_) =>
              const FlLine(color: AppColors.track, strokeWidth: 1),
        ),
        borderData: FlBorderData(show: false),
        titlesData: FlTitlesData(
          topTitles:
              const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          rightTitles:
              const AxisTitles(sideTitles: SideTitles(showTitles: false)),
          leftTitles: const AxisTitles(
            sideTitles: SideTitles(showTitles: true, reservedSize: 28),
          ),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 28,
              getTitlesWidget: (value, meta) {
                final i = value.toInt();
                if (i < 0 || i >= daily.length) return const SizedBox.shrink();
                final parts = daily[i].date.split('-');
                if (parts.length != 3) return const SizedBox.shrink();
                return Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Text('${int.parse(parts[1])}/${int.parse(parts[2])}',
                      style: const TextStyle(
                          fontSize: 11, color: AppColors.inkSecondary)),
                );
              },
            ),
          ),
        ),
        lineBarsData: [
          LineChartBarData(
            spots: [
              for (var i = 0; i < daily.length; i++)
                FlSpot(i.toDouble(), daily[i].interactionCount.toDouble()),
            ],
            isCurved: true,
            curveSmoothness: 0.25,
            color: AppColors.accent,
            barWidth: 3,
            dotData: const FlDotData(show: true),
            belowBarData: BarAreaData(
              show: true,
              color: AppColors.accent.withValues(alpha: 0.12),
            ),
          ),
        ],
      ),
    );
  }
}

/// 數值備援：圖表以外一定要能讀到數字（§14 a11y fallback）。
class _DailyNumbers extends StatelessWidget {
  const _DailyNumbers({required this.daily});

  final List<DailyStat> daily;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Column(
      children: [
        for (final d in daily)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 5),
            child: Row(
              children: [
                SizedBox(
                  width: 64,
                  child: Text(_short(d.date),
                      style: text.bodySmall
                          ?.copyWith(color: AppColors.inkSecondary)),
                ),
                Expanded(
                  child:
                      Text('${d.interactionCount} 次對話', style: text.bodyMedium),
                ),
                Text('例行 ${d.routinesCompleted}/${d.routinesTotal}',
                    style: text.bodySmall
                        ?.copyWith(color: AppColors.inkSecondary)),
              ],
            ),
          ),
      ],
    );
  }

  static String _short(String iso) {
    final p = iso.split('-');
    if (p.length != 3) return iso;
    return '${int.parse(p[1])} 月 ${int.parse(p[2])} 日';
  }
}
