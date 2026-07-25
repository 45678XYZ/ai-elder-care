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

  /// 健康註記。
  final List<String> healthNotes;
  final List<FamilyMember> family;

  /// 生活習慣描述。
  final String? habitNote;
  final DateTime? createdAt;

  /// 後端只在成功變更時刷新；建立當下與 [createdAt] 相同。
  final DateTime? updatedAt;

  factory Elder.fromJson(Map<String, dynamic> json) => Elder(
        elderId: json['elder_id'] as String? ?? '',
        name: json['name'] as String? ?? '',
        nickname: json['nickname'] as String?,
        birthYear: json['birth_year'] as int?,
        gender: json['gender'] as String?,
        langPreference: json['lang_preference'] as String? ?? 'zh-TW',
        addressRegion: json['address_region'] as String?,
        healthNotes: (json['health_notes'] as List<dynamic>?)
                ?.map((e) => e as String)
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
