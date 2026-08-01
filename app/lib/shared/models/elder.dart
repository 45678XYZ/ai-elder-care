/// 長者資料。欄位規格見 docs/api.md（GET /elders）。
class Elder {
  const Elder({
    required this.elderId,
    required this.name,
    this.nickname,
    this.birthYear,
    this.gender,
    required this.langPreference,
    this.hakkaDialect = 'htia_sixian',
    this.addressRegion,
    this.healthNotes = const [],
    this.family = const [],
    this.habitNote,
    this.createdAt,
    this.updatedAt,
  });

  final String elderId;
  final String name;
  final String? nickname;
  final int? birthYear;

  final String? gender;

  /// 語言偏好；可用值見 docs/api.md。
  final String langPreference;

  /// 客語腔調，六選一（見 [HakkaDialect]）。**只在 [langPreference] 是 `hak` 時有意義。**
  ///
  /// api.md：「客語腔調是 ASR/TTS 唯一來源」，而且「後端只讀 elder profile 的
  /// `hakka_dialect`，App 不在 `/chat` 傳腔調」——所以這個值一定要寫進長者檔案，
  /// 存在本機沒有任何效果。這也是它跟 [langPreference] 的關鍵差別：後者每次
  /// `/chat` 都會帶上去，前者不會。
  final String hakkaDialect;

  /// 居住地區（如「台北市大安區」）。
  final String? addressRegion;

  /// 健康註記。每一筆帶來源，見 [HealthNote]。
  final List<HealthNote> healthNotes;
  final List<FamilyMember> family;

  /// 生活習慣描述。
  final String? habitNote;
  final DateTime? createdAt;

  /// 後端只在成功變更時刷新；建立當下與 [createdAt] 相同。
  final DateTime? updatedAt;

  /// 複製並覆寫部分欄位（對應 `PATCH /elders/{id}` 的部分更新語意）。
  ///
  /// 可為 null 的欄位省略時保留原值，**不提供「改成 null」**：api.md 的 PATCH 是
  /// 部分更新，沒有清空單一欄位的語意，這裡也就不做得比契約更多。
  Elder copyWith({
    String? name,
    String? nickname,
    int? birthYear,
    String? gender,
    String? langPreference,
    String? hakkaDialect,
    String? addressRegion,
    List<HealthNote>? healthNotes,
    List<FamilyMember>? family,
    String? habitNote,
    DateTime? updatedAt,
  }) =>
      Elder(
        elderId: elderId,
        name: name ?? this.name,
        nickname: nickname ?? this.nickname,
        birthYear: birthYear ?? this.birthYear,
        gender: gender ?? this.gender,
        langPreference: langPreference ?? this.langPreference,
        hakkaDialect: hakkaDialect ?? this.hakkaDialect,
        addressRegion: addressRegion ?? this.addressRegion,
        healthNotes: healthNotes ?? this.healthNotes,
        family: family ?? this.family,
        habitNote: habitNote ?? this.habitNote,
        createdAt: createdAt,
        updatedAt: updatedAt ?? this.updatedAt,
      );

  factory Elder.fromJson(Map<String, dynamic> json) => Elder(
        elderId: json['elder_id'] as String? ?? '',
        name: json['name'] as String? ?? '',
        nickname: json['nickname'] as String?,
        birthYear: json['birth_year'] as int?,
        gender: json['gender'] as String?,
        langPreference: json['lang_preference'] as String? ?? 'zh-TW',
        hakkaDialect:
            json['hakka_dialect'] as String? ?? HakkaDialect.defaultValue,
        addressRegion: json['address_region'] as String?,
        healthNotes: (json['health_notes'] as List<dynamic>?)
                ?.map(HealthNote.fromJson)
                .toList() ??
            const [],
        family: (json['family'] as List<dynamic>?)
                ?.map((e) => FamilyMember.fromJson(e as Map<String, dynamic>))
                .toList() ??
            const [],
        habitNote: json['habit_note'] as String?,
        createdAt: json['created_at'] == null
            ? null
            : DateTime.tryParse(json['created_at'] as String),
        updatedAt: json['updated_at'] == null
            ? null
            : DateTime.tryParse(json['updated_at'] as String),
      );
}

/// 客語腔調。值與 api.md 的 `hakka_dialect` enum 一一對應，**不可自創**——
/// 後端對其他值回 400 `INVALID_PARAMETER`。
///
/// 六腔各自有獨立的 ASR 與 TTS 模型端點（terraform/asr_models.tf、tts_models.tf），
/// 所以選錯不是「口音不太像」而是**辨識不出來**：四縣的長輩被設成海陸，他每一句
/// 都會失敗。這也是為什麼不能只列常見的幾種了事。
///
/// 宣告順序即畫面上的排列順序，四縣排第一（api.md 的預設）。
enum HakkaDialect {
  sixian('htia_sixian', '四縣'),
  hailu('htia_hailu', '海陸'),
  dapu('htia_dapu', '大埔'),
  raoping('htia_raoping', '饒平'),
  zhaoan('htia_zhaoan', '詔安'),
  nansixian('htia_nansixian', '南四縣');

  const HakkaDialect(this.value, this.label);

  /// 送給後端的字串（api.md 的 enum 值）。
  final String value;

  /// 畫面上顯示的腔調名。兩種書寫語言下相同，是專有名詞，不進 i18n 對照表。
  ///
  /// **不附範例句**：六腔的例句要有可靠來源才敢放，來源查不到就不放——寫錯的
  /// 客語比沒有客語更糟，長輩聽到不對的腔會選錯。
  final String label;

  /// api.md 的預設腔調。認不得的值一律落回這裡，不讓畫面顯示空白。
  static const defaultValue = 'htia_sixian';

  /// 後端字串 → 列舉；未知值回 [sixian]（與後端預設一致）。
  static HakkaDialect fromValue(String? v) {
    for (final d in values) {
      if (d.value == v) return d;
    }
    return sixian;
  }
}

/// 單筆健康註記。欄位規格見 docs/api.md 的 health_notes 物件。
///
/// **來源要分得出來**：這個欄位同時被照護者（自己填的）與對話中的 AI（依長輩談話
/// 補上的）寫入。AI 聽來的那幾筆更可能出錯、也更需要照護者確認，畫面上不能跟
/// 手填的長成一樣。
class HealthNote {
  const HealthNote({
    required this.noteId,
    required this.text,
    this.source = HealthNoteSource.caregiver,
    this.createdAt,
  });

  final String noteId;
  final String text;
  final HealthNoteSource source;
  final DateTime? createdAt;

  /// 相容舊格式的純字串：後端把它們一律視為照護者填的，這裡跟著同一套規則，
  /// 免得同一份資料在前後端算出不同來源。
  factory HealthNote.fromJson(dynamic json) {
    if (json is String) {
      return HealthNote(noteId: '', text: json);
    }
    final map = json as Map<String, dynamic>;
    return HealthNote(
      noteId: map['note_id'] as String? ?? '',
      text: map['text'] as String? ?? '',
      source: map['source'] == 'agent'
          ? HealthNoteSource.agent
          : HealthNoteSource.caregiver,
      createdAt: map['created_at'] == null
          ? null
          : DateTime.tryParse(map['created_at'] as String),
    );
  }
}

/// 健康註記的來源。
enum HealthNoteSource {
  /// 照護者在 App 上自己填的。
  caregiver,

  /// 對話中由 AI 依長輩談話補上的。
  agent,
}

/// 長者的家屬成員。
class FamilyMember {
  const FamilyMember({
    required this.relation,
    required this.name,
    this.note,
  });

  final String relation;
  final String name;
  final String? note;

  factory FamilyMember.fromJson(Map<String, dynamic> json) => FamilyMember(
        relation: json['relation'] as String? ?? '',
        name: json['name'] as String? ?? '',
        note: json['note'] as String?,
      );

  /// 送 `PATCH /elders/{id}` 的 `family` 時用。
  ///
  /// 空的 note 不送，讓後端保持它自己的預設，不要塞一個空字串進去。
  Map<String, dynamic> toJson() => {
        'relation': relation,
        'name': name,
        if (note != null && note!.trim().isNotEmpty) 'note': note,
      };
}
