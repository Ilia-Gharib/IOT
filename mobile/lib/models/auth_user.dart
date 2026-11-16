class AuthUser {
  final String username;
  final String email;
  final String role;
  final int floor;
  final List<int> allowedFloors;
  final String token;
  final int expiresIn;

  AuthUser({
    required this.username,
    required this.email,
    required this.role,
    required this.floor,
    required this.allowedFloors,
    required this.token,
    required this.expiresIn,
  });

  factory AuthUser.fromJson(Map<String, dynamic> json) {
    final floorVal = json['floor'];
    int parsedFloor = 1;
    if (floorVal is int) {
      parsedFloor = floorVal;
    } else if (floorVal != null) {
      parsedFloor = int.tryParse(floorVal.toString()) ?? 1;
    }

    List<int> parsedAllowedFloors = [parsedFloor];
    if (json['allowed_floors'] is List) {
      parsedAllowedFloors = (json['allowed_floors'] as List<dynamic>)
          .map((e) => e is int ? e : int.tryParse(e.toString()) ?? parsedFloor)
          .toList();
    }

    final rawToken = json['token'] ?? json['access_token'] ?? '';
    final usernameVal = json['username'] as String? ??
        json['full_name'] as String? ??
        json['name'] as String? ??
        '';

    return AuthUser(
      username: usernameVal,
      email: json['email'] as String? ?? '',
      role: json['role'] as String? ?? 'resident',
      floor: parsedFloor,
      allowedFloors: parsedAllowedFloors.isNotEmpty ? parsedAllowedFloors : [parsedFloor],
      token: rawToken.toString(),
      expiresIn: json['expires_in'] is int
          ? json['expires_in'] as int
          : int.tryParse(json['expires_in']?.toString() ?? '') ?? 43200,
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'username': username,
      'email': email,
      'role': role,
      'floor': floor,
      'allowed_floors': allowedFloors,
      'token': token,
      'expires_in': expiresIn,
    };
  }
}
