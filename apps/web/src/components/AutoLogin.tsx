import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiJson } from '@/shared/api/client';
import { useAuth } from '../hooks/useAuth';

/**
 * 自动登录组件 - 用于展示/演示模式
 * 自动使用管理员账户登录，无需手动输入
 */
export function AutoLogin() {
  const [status, setStatus] = useState<'logging' | 'success' | 'error'>('logging');
  const navigate = useNavigate();
  const { setSession } = useAuth();

  useEffect(() => {
    const autoLogin = async () => {
      try {
        // 检查是否已经登录
        const existingToken = localStorage.getItem('access_token');
        if (existingToken) {
          setStatus('success');
          navigate('/', { replace: true });
          return;
        }

        // 演示模式下由后端按配置签发令牌，前端不携带明文密码。
        const data = await apiJson<{ access_token: string; user: unknown }>('/api/auth/demo-login', {
          method: 'POST',
        });

        setSession(data.access_token, data.user as Parameters<typeof setSession>[1]);

        setStatus('success');

        navigate('/', { replace: true });

      } catch (error) {
        console.error('自动登录失败:', error);
        setStatus('error');
        // 3秒后跳转到登录页面
        setTimeout(() => {
          navigate('/login');
        }, 3000);
      }
    };

    autoLogin();
  }, [navigate, setSession]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 to-indigo-100">
      <div className="max-w-md w-full space-y-8 bg-white p-10 rounded-xl shadow-2xl text-center">
        {/* Logo */}
        <div>
          <h1 className="text-4xl font-bold text-gray-900 mb-2">SIQ</h1>
          <p className="text-gray-600">财务分析工作台</p>
        </div>

        {/* 状态显示 */}
        <div className="py-8">
          {status === 'logging' && (
            <div className="space-y-4">
              <div className="flex justify-center">
                <div className="animate-spin rounded-full h-16 w-16 border-b-2 border-indigo-600"></div>
              </div>
              <p className="text-gray-600">正在进入系统...</p>
              <p className="text-sm text-gray-400">演示模式：自动登录中</p>
            </div>
          )}

          {status === 'success' && (
            <div className="space-y-4">
              <div className="flex justify-center">
                <svg className="h-16 w-16 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <p className="text-gray-600 font-medium">登录成功！</p>
              <p className="text-sm text-gray-400">正在跳转到主页...</p>
            </div>
          )}

          {status === 'error' && (
            <div className="space-y-4">
              <div className="flex justify-center">
                <svg className="h-16 w-16 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <p className="text-gray-600 font-medium">自动登录失败</p>
              <p className="text-sm text-gray-400">即将跳转到登录页面...</p>
            </div>
          )}
        </div>

        {/* 提示信息 */}
        <div className="border-t border-gray-200 pt-6">
          <p className="text-xs text-gray-500">
            🎯 展示模式：系统将自动以管理员身份登录
          </p>
        </div>
      </div>
    </div>
  );
}
