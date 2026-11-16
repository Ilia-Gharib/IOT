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
    return AuthUser(
      username: json['username'] as String? ?? '',
      email: json['email'] as String? ?? '',
      role: json['role'] as String? ?? 'resident',
      floor: json['floor'] is int ? json['floor'] as int : 1,
      allowedFloors: (json['allowed_floors'] as List<dynamic>?)
              ?.map((e) => e is int ? e : int.tryParse(e.toString()) ?? 1)
              .toList() ??
          [1],
      token: json['token'] as String? ?? '',
      expiresIn: json['expires_in'] is int ? json['expires_in'] as int : 43200,
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
