/// 長者資料。欄位規格見 docs/api.md（GET /elders）。
class Elder {
  const Elder({
    required this.elderId,
    required this.name,
    this.nickname,
    this.birthYear,
    this.gender,
    required this.langPreference,
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
}
