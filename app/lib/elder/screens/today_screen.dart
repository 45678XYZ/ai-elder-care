import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../shared/models/routine.dart';
import '../../shared/services/demo_data.dart';
import '../../shared/services/lunar_date.dart';
import '../../shared/widgets/app_card.dart';
import '../../shared/widgets/async_view.dart';
import '../../shared/widgets/status_chip.dart';
import '../../theme/app_theme.dart';

/// S4 `/elder/today` — 長者模式今日畫面。
///
/// 上半農民曆牌面（傳統撕曆版面：國曆年、歲次、月份直排、農曆直排、巨大日期），下半當日行程
/// （`GET /routines?elder_id=&date=`），可手動確認完成（`POST /routines/{id}/complete`）。
///
/// 長者規格：內文 >=24sp、觸控 >=60dp、**單頁可互動元素 <=3**。
/// 為了守住最後一條，只有「接下來那一件」給確認按鈕，其餘行程純顯示不可點——
/// 長者不必在一整列按鈕裡挑，要做的事永遠只有畫面上最大的那一個。
///
/// 不放語言切換：語言由照護者在初次設定決定，長者端不切換（見 setup_screen §5.1）。
class TodayScreen extends StatefulWidget {
  const TodayScreen({super.key});

  @override
  State<TodayScreen> createState() => _TodayScreenState();
}

class _TodayScreenState extends State<TodayScreen> {
  late Future<DailyRoutineView> _future;

  /// 本地已確認完成的 routine——按下去立刻反映，不等重新拉整份清單。
  final _justCompleted = <String>{};

  @override
  void initState() {
    super.initState();
    _load();
  }

  void _load() {
    // TODO: 後端上線後改為 api.getDailyRoutines(elderId: ..., date: ...)
    _future = DemoData.dailyRoutines(_dateKey(DateTime.now()));
  }

  void _reload() => setState(_load);

  static String _dateKey(DateTime d) =>
      '${d.year}-${_two(d.month)}-${_two(d.day)}';

  static String _two(int v) => v.toString().padLeft(2, '0');

  /// 確認完成。§13：成功要有明確回饋，長者確認尤其要——這裡用觸覺＋提示條＋狀態改變三重。
  Future<void> _complete(RoutineOccurrence o) async {
    HapticFeedback.mediumImpact();
    setState(() => _justCompleted.add(o.routineId));
    try {
      // TODO: 後端上線後改為 api.completeRoutine(o.routineId)
      await DemoData.completeRoutine(o);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          backgroundColor: AppColors.barDark,
          duration: const Duration(seconds: 3),
          content: Text(
            '「${o.title}」已記錄完成',
            style: Theme.of(context)
                .textTheme
                .headlineSmall
                ?.copyWith(color: AppColors.onDark),
          ),
        ),
      );
    } catch (_) {
      // 失敗就把樂觀更新收回來，讓長者看得到它其實沒完成。
      if (mounted) setState(() => _justCompleted.remove(o.routineId));
    }
  }

  String _statusOf(RoutineOccurrence o) =>
      _justCompleted.contains(o.routineId) ? 'done' : o.status;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.app,
      body: SafeArea(
        child: AsyncView<DailyRoutineView>(
          future: _future,
          onRetry: _reload,
          elderMode: true,
          isEmpty: (v) => v.items.isEmpty,
          emptyIcon: Icons.event_available_outlined,
          emptyText: '今天沒有安排喔',
          builder: (context, view) {
            final items = view.items.toList()
              ..sort((a, b) => a.scheduledAt.compareTo(b.scheduledAt));
            // 「接下來」＝時間最早、還沒完成的那一件（含先前漏掉的）。
            RoutineOccurrence? next;
            for (final o in items) {
              if (_statusOf(o) != 'done') {
                next = o;
                break;
              }
            }
            final pending = next;

            return ListView(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
              children: [
                const _AlmanacPanel(),
                const SizedBox(height: AppSpacing.xl),
                if (pending != null) ...[
                  _NextUpCard(
                    occurrence: pending,
                    status: _statusOf(pending),
                    onComplete: () => _complete(pending),
                  ),
                  const SizedBox(height: AppSpacing.xl),
                ],
                const SectionHeader('今天的安排', elderMode: true),
                const SizedBox(height: AppSpacing.md),
                for (final o in items) ...[
                  _RoutineRow(
                    key: ValueKey(o.routineId),
                    occurrence: o,
                    status: _statusOf(o),
                    isNext: identical(o, pending),
                  ),
                  const SizedBox(height: AppSpacing.md),
                ],
              ],
            );
          },
        ),
      ),
    );
  }
}

/// 農民曆牌面——照傳統撕曆的版面：
///
/// ```
/// 2026      歲次丙午年      7
///                          月
/// 農
/// 曆          19
/// 六
/// 月        星 期 日
/// 初
/// 六
/// ```
///
/// 四角各司其職（左上國曆年、中上干支、右上月份直排、左側農曆直排），
/// 中央留給巨大的日期。全朱紅單色，靠字級與位置分層次，不加第二個顏色。
class _AlmanacPanel extends StatelessWidget {
  const _AlmanacPanel();

  static const _weekdays = ['一', '二', '三', '四', '五', '六', '日'];

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final now = DateTime.now();
    final lunar = LunarDate.of(now);

    return AppCard(
      // 牌面用最白的紙色，跟下方一般卡片（card）區隔開，像一張撕曆貼在頁面上
      color: AppColors.cardAlt,
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 18),
      radius: AppRadius.cardLarge,
      shadows: AppShadows.cardRaised,
      semanticLabel: '${now.month}月${now.day}日 星期${_weekdays[now.weekday - 1]}，'
          '農曆${lunar.monthDay}，歲次${lunar.ganZhiYear}年'
          '${lunar.highlight == null ? '' : '，${lunar.highlight}'}',
      child: Column(
        children: [
          // 頂列：國曆年 · 歲次 · 月份（數字大、「月」小，直排）
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('${now.year}', style: AlmanacTypography.year),
              Expanded(
                child: Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: Text('歲次${lunar.ganZhiYear}年',
                      textAlign: TextAlign.center,
                      style: AlmanacTypography.ganZhi),
                ),
              ),
              Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text('${now.month}', style: AlmanacTypography.monthNumber),
                  Text('月', style: AlmanacTypography.monthLabel),
                ],
              ),
            ],
          ),

          // 中段：農曆直排靠左，巨大日期置中
          Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              _VerticalText('農曆${lunar.monthDay}',
                  style: AlmanacTypography.lunar,
                  gap: AlmanacTypography.lunarGap),
              // 日期在「農曆直排右緣」到「卡片右緣」之間置中——與左右兩側等距，
              // 而不是對齊整張卡片的中線（那會被左邊的直排推得偏右）。
              // FittedBox：200sp 在窄螢幕或系統放大字級時自動縮，不撐破卡片。
              Expanded(
                child: Center(
                  child: FittedBox(
                    fit: BoxFit.scaleDown,
                    child: Text('${now.day}', style: AlmanacTypography.day),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),

          // letterSpacing 會在最後一個字右側也加上間距，補一個左邊距讓它視覺置中
          Padding(
            padding: EdgeInsets.only(
                left: AlmanacTypography.weekday.letterSpacing ?? 0),
            child: Text('星期${_weekdays[now.weekday - 1]}',
                style: AlmanacTypography.weekday),
          ),

          // 節氣或農曆節日只在當天出現
          if (lunar.highlight != null) ...[
            const SizedBox(height: AppSpacing.md),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 8),
              decoration: const BoxDecoration(
                color: AppColors.accentText,
                borderRadius: BorderRadius.all(AppRadius.pill),
              ),
              child: Text(lunar.highlight!,
                  style: text.headlineSmall?.copyWith(color: Colors.white)),
            ),
          ],
        ],
      ),
    );
  }
}

/// 中文直排：逐字往下排。
///
/// 不用 RotatedBox——那會把字也轉倒。中文直排本來就是「字不轉、往下疊」。
/// [gap] 是字與字的垂直間隔（直排的「字距」）；`letterSpacing` 在直排無效，
/// 因為每個字各自是一個 Text。
class _VerticalText extends StatelessWidget {
  const _VerticalText(this.text, {this.style, this.gap = 0});

  final String text;
  final TextStyle? style;
  final double gap;

  @override
  Widget build(BuildContext context) {
    final runes = text.runes.toList();
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        for (var i = 0; i < runes.length; i++) ...[
          if (i > 0) SizedBox(height: gap),
          Text(String.fromCharCode(runes[i]), style: style),
        ],
      ],
    );
  }
}

/// 「接下來」——本畫面唯一的可互動元素。
class _NextUpCard extends StatelessWidget {
  const _NextUpCard({
    required this.occurrence,
    required this.status,
    required this.onComplete,
  });

  final RoutineOccurrence occurrence;
  final String status;
  final VoidCallback onComplete;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final missed = status == 'missed';

    return AppCard(
      color: AppColors.cardAlt,
      padding: const EdgeInsets.all(20),
      radius: AppRadius.cardLarge,
      shadows: AppShadows.cardRaised,
      border: Border.all(color: AppColors.accent, width: 2),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(missed ? '這件還沒做' : '接下來',
              style: text.headlineSmall?.copyWith(color: AppColors.accentText)),
          const SizedBox(height: AppSpacing.sm),
          Text(occurrence.title, style: text.headlineLarge),
          const SizedBox(height: AppSpacing.sm),
          Row(
            children: [
              const Icon(Icons.schedule,
                  size: 28, color: AppColors.inkSecondary),
              const SizedBox(width: AppSpacing.sm),
              Flexible(
                child: Text(_timeLabel(occurrence.scheduledAt),
                    style: text.headlineSmall
                        ?.copyWith(color: AppColors.inkSecondary)),
              ),
            ],
          ),
          const SizedBox(height: AppSpacing.lg),
          SizedBox(
            width: double.infinity,
            height: 72, // >=60dp
            child: FilledButton.icon(
              onPressed: onComplete,
              icon: const Icon(Icons.check, size: 32),
              label: Text('我完成了',
                  style: text.headlineMedium?.copyWith(color: Colors.white)),
              style: FilledButton.styleFrom(
                backgroundColor: AppColors.accentText,
                foregroundColor: Colors.white,
                shape: const RoundedRectangleBorder(
                  borderRadius: BorderRadius.all(AppRadius.field),
                ),
              ).copyWith(
                overlayColor:
                    const WidgetStatePropertyAll(AppColors.accentPressed),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// 行程列——純顯示，不可點（可互動元素只留給「接下來」那張卡）。
class _RoutineRow extends StatelessWidget {
  const _RoutineRow({
    super.key,
    required this.occurrence,
    required this.status,
    required this.isNext,
  });

  final RoutineOccurrence occurrence;
  final String status;
  final bool isNext;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final done = status == 'done';

    return AppCard(
      color: done ? AppColors.nest : AppColors.card,
      padding: const EdgeInsets.symmetric(vertical: 16, horizontal: 16),
      border: isNext ? Border.all(color: AppColors.accent, width: 2) : null,
      semanticLabel: '${occurrence.title}，'
          '${_timeLabel(occurrence.scheduledAt)}，'
          '${RoutineStatusStyle.from(status).label}',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Text(
                  occurrence.title,
                  style: text.headlineSmall?.copyWith(
                    color: done ? AppColors.inkSecondary : AppColors.ink,
                    decoration: done ? TextDecoration.lineThrough : null,
                    decorationColor: AppColors.inkSecondary,
                  ),
                ),
              ),
              const SizedBox(width: AppSpacing.sm),
              Text(_timeLabel(occurrence.scheduledAt),
                  style: text.headlineSmall
                      ?.copyWith(color: AppColors.inkSecondary)),
            ],
          ),
          const SizedBox(height: AppSpacing.sm),
          // 換行擺放，textScaler 2.0 下狀態膠囊不會跟標題擠在同一列
          Align(
            alignment: Alignment.centerLeft,
            child: RoutineStatusChip(status, elderMode: true),
          ),
        ],
      ),
    );
  }
}

String _timeLabel(DateTime t) =>
    '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
