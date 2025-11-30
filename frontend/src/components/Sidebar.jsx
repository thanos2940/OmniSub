import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { Home, Settings, Box, LayoutGrid } from 'lucide-react';
import { motion } from 'framer-motion';

const Sidebar = () => {
    const location = useLocation();

    const menuItems = [
        { icon: Home, label: 'Projects', path: '/' },
        { icon: Box, label: 'Tools', path: '/tools' },
        { icon: Settings, label: 'Settings', path: '/settings' },
    ];

    return (
        <div className="fixed left-0 top-0 h-screen w-64 bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-800 flex flex-col z-50">
            <div className="p-6 flex items-center gap-3">
                <div className="w-8 h-8 bg-indigo-600 rounded-lg flex items-center justify-center">
                    <LayoutGrid className="text-white" size={20} />
                </div>
                <h1 className="text-xl font-bold text-gray-900 dark:text-white tracking-tight">OmbiSub</h1>
            </div>

            <nav className="flex-1 px-4 space-y-2 mt-4">
                {menuItems.map((item) => {
                    const isActive = location.pathname === item.path || (item.path !== '/' && location.pathname.startsWith(item.path));
                    return (
                        <Link
                            key={item.path}
                            to={item.path}
                            className={`flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-200 group relative ${isActive
                                    ? 'text-indigo-600 dark:text-indigo-400 font-medium'
                                    : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white hover:bg-gray-50 dark:hover:bg-gray-800'
                                }`}
                        >
                            {isActive && (
                                <motion.div
                                    layoutId="sidebar-active"
                                    className="absolute inset-0 bg-indigo-50 dark:bg-indigo-900/20 rounded-xl"
                                    initial={false}
                                    transition={{ type: "spring", stiffness: 300, damping: 30 }}
                                />
                            )}
                            <item.icon size={20} className="relative z-10" />
                            <span className="relative z-10">{item.label}</span>
                        </Link>
                    );
                })}
            </nav>

            <div className="p-4 border-t border-gray-200 dark:border-gray-800">
                <div className="bg-gradient-to-br from-indigo-500 to-purple-600 rounded-xl p-4 text-white">
                    <p className="text-xs font-medium opacity-80 mb-1">Current Plan</p>
                    <p className="text-sm font-bold">Pro License</p>
                </div>
            </div>
        </div>
    );
};

export default Sidebar;
