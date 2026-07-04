#!/usr/bin/env python3
"""
初始化认证系统数据库和创建初始管理员账户（使用SQLModel）
"""
import sys
import os
from pathlib import Path

# 添加backend到路径
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

os.chdir(backend_dir)


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"请先设置环境变量 {name}")
    return value


def _required_env_any(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    raise RuntimeError(f"请先设置环境变量 {' 或 '.join(names)}")


def _env_enabled(*names: str) -> bool:
    for name in names:
        value = os.getenv(name)
        if value is not None:
            return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def init_database():
    """初始化数据库表"""
    try:
        from sqlmodel import SQLModel, create_engine
        from services.auth_service import User, AuditLog, ReportReview

        print("📦 连接数据库...")

        database_url = _required_env_any("SIQ_APP_DATABASE_URL", "DATABASE_URL")

        print(f"🔗 数据库URL: {database_url.replace(':@', ':***@')}")

        engine = create_engine(database_url, echo=False)

        print("🔨 创建数据库表...")
        SQLModel.metadata.create_all(engine)

        print("✅ 数据库表创建成功")
        print(f"   - users: 用户表")
        print(f"   - audit_logs: 审计日志表")
        print(f"   - report_reviews: 报告审核表")

        return True

    except Exception as e:
        print(f"❌ 数据库初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def create_admin_user():
    """创建初始管理员账户"""
    try:
        from services.auth_service import User, AuthService
        from sqlmodel import Session, create_engine, select

        print("\n👤 创建初始管理员账户...")

        database_url = _required_env_any("SIQ_APP_DATABASE_URL", "DATABASE_URL")
        admin_password = _required_env_any("SIQ_INITIAL_ADMIN_PASSWORD", "SIQ_INITIAL_ADMIN_PASSWORD")
        if len(admin_password) < 12:
            raise RuntimeError("SIQ_INITIAL_ADMIN_PASSWORD / SIQ_INITIAL_ADMIN_PASSWORD 至少需要 12 个字符")
        engine = create_engine(database_url, echo=False)

        with Session(engine) as session:
            # 检查是否已存在管理员
            existing = session.exec(select(User).where(User.username == 'admin')).first()
            if existing:
                print("⚠️  管理员账户已存在，跳过创建")
                print(f"   用户名: {existing.username}")
                print(f"   邮箱: {existing.email}")
                print(f"   角色: {existing.role}")
                return True

            # 创建管理员
            admin = User(
                username='admin',
                email='admin@siq.local',
                hashed_password=AuthService.hash_password(admin_password),
                full_name='系统管理员',
                role='super_admin',
                is_active=True
            )

            session.add(admin)
            session.commit()
            session.refresh(admin)

            print("✅ 管理员账户创建成功")
            print(f"   用户ID: {admin.id}")
            print(f"   用户名: {admin.username}")
            print(f"   邮箱: {admin.email}")
            print(f"   角色: {admin.role}")
            print("   密码: 已从 SIQ_INITIAL_ADMIN_PASSWORD / SIQ_INITIAL_ADMIN_PASSWORD 读取，不在日志中显示")

            return True

    except Exception as e:
        print(f"❌ 创建管理员失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def create_demo_users():
    """创建演示用户"""
    try:
        from services.auth_service import User, AuthService
        from sqlmodel import Session, create_engine, select

        print("\n👥 创建演示用户...")

        demo_users = [
            {
                'username': 'analyst01',
                'email': 'analyst@siq.local',
                'password': _required_env_any("SIQ_DEMO_ANALYST_PASSWORD", "SIQ_DEMO_ANALYST_PASSWORD"),
                'full_name': '张三（分析师）',
                'role': 'analyst',
            },
            {
                'username': 'reviewer01',
                'email': 'reviewer@siq.local',
                'password': _required_env_any("SIQ_DEMO_REVIEWER_PASSWORD", "SIQ_DEMO_REVIEWER_PASSWORD"),
                'full_name': '李四（复核员）',
                'role': 'reviewer',
            },
            {
                'username': 'viewer01',
                'email': 'viewer@siq.local',
                'password': _required_env_any("SIQ_DEMO_VIEWER_PASSWORD", "SIQ_DEMO_VIEWER_PASSWORD"),
                'full_name': '王五（查看者）',
                'role': 'viewer',
            },
        ]

        if not _env_enabled("SIQ_CREATE_DEMO_USERS", "SIQ_CREATE_DEMO_USERS"):
            print("   演示用户创建已关闭，设置 SIQ_CREATE_DEMO_USERS=1 可启用。")
            return True

        database_url = _required_env_any("SIQ_APP_DATABASE_URL", "DATABASE_URL")
        engine = create_engine(database_url, echo=False)

        with Session(engine) as session:
            created_count = 0
            for user_data in demo_users:
                # 检查是否已存在
                existing = session.exec(
                    select(User).where(User.username == user_data['username'])
                ).first()

                if existing:
                    print(f"   ⚠️  {user_data['username']} 已存在，跳过")
                    continue

                # 创建用户
                user = User(
                    username=user_data['username'],
                    email=user_data['email'],
                    hashed_password=AuthService.hash_password(user_data['password']),
                    full_name=user_data['full_name'],
                    role=user_data['role'],
                    is_active=True
                )

                session.add(user)
                created_count += 1

            if created_count > 0:
                session.commit()
                print(f"\n✅ 创建了 {created_count} 个演示用户")
                for user_data in demo_users:
                    print(f"   ✅ {user_data['full_name']} ({user_data['username']})")

            return True

    except Exception as e:
        print(f"❌ 创建演示用户失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_login():
    """测试登录功能"""
    try:
        from services.auth_service import User, AuthService
        from sqlmodel import Session, create_engine, select

        print("\n🔐 测试登录功能...")

        database_url = _required_env_any("SIQ_APP_DATABASE_URL", "DATABASE_URL")
        admin_password = _required_env_any("SIQ_INITIAL_ADMIN_PASSWORD", "SIQ_INITIAL_ADMIN_PASSWORD")
        engine = create_engine(database_url, echo=False)

        with Session(engine) as session:
            # 获取管理员
            admin = session.exec(select(User).where(User.username == 'admin')).first()
            if not admin:
                print("❌ 找不到管理员账户")
                return False

            # 验证密码
            is_valid = AuthService.verify_password(admin_password, admin.hashed_password)
            if is_valid:
                print("✅ 密码验证成功")

                # 生成JWT令牌
                token = AuthService.create_access_token({
                    'sub': admin.username,
                    'role': admin.role
                })
                print(f"✅ JWT令牌生成成功")
                print(f"   Token前50字符: {token[:50]}...")

                # 解码令牌
                payload = AuthService.decode_token(token)
                if payload:
                    print(f"✅ 令牌解码成功")
                    print(f"   用户: {payload.get('sub')}")
                    print(f"   角色: {payload.get('role')}")
                    return True
                else:
                    print("❌ 令牌解码失败")
                    return False
            else:
                print("❌ 密码验证失败")
                return False

    except Exception as e:
        print(f"❌ 登录测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主函数"""
    print("="*60)
    print("SIQ Research Engine 认证系统初始化")
    print("="*60)

    # 1. 初始化数据库
    if not init_database():
        print("\n❌ 初始化失败，请检查数据库连接")
        print("\n💡 提示：")
        print("   1. 确保PostgreSQL正在运行")
        print("   2. 确保 siq_app 应用状态数据库存在")
        print("   3. 设置 SIQ_APP_DATABASE_URL、SIQ_AUTH_SECRET_KEY 和 SIQ_INITIAL_ADMIN_PASSWORD")
        return False

    # 2. 创建管理员
    if not create_admin_user():
        print("\n❌ 创建管理员失败")
        return False

    # 3. 创建演示用户
    if not create_demo_users():
        print("\n⚠️  创建演示用户失败，但可以继续")

    # 4. 测试登录
    if not test_login():
        print("\n⚠️  登录测试失败，但账户已创建")

    print("\n" + "="*60)
    print("✅ 认证系统初始化完成")
    print("="*60)
    print("\n📋 账户信息：")
    print("   管理员: admin / <SIQ_INITIAL_ADMIN_PASSWORD>")
    print("   演示用户: 默认不创建；设置 SIQ_CREATE_DEMO_USERS=1 后才会创建")
    print("\n🌐 登录地址: http://localhost:15173/login")
    print("\n⚠️  首次登录后请修改密码！")

    return True


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
