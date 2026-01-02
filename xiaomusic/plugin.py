import importlib
import inspect
import pkgutil


class PluginManager:
    """
    Python 插件管理器
    負責加載和執行 Python 編寫的插件模組
    注意：這與 js_plugin_manager 不同，後者管理 JavaScript 插件
    """
    def __init__(self, xiaomusic, plugin_dir="plugins"):
        self.xiaomusic = xiaomusic
        self.log = xiaomusic.log
        self._funcs = {}
        self._load_plugins(plugin_dir)

    def _load_plugins(self, plugin_dir):
        """
        動態加載指定目錄下的所有 Python 插件
        插件目錄需為 Python 包結構
        """
        # 假設 plugins 已經在搜索路徑上
        package_name = plugin_dir
        package = importlib.import_module(package_name)

        # 遍歷 package 中所有模塊並動態導入它們
        for _, modname, _ in pkgutil.iter_modules(package.__path__, package_name + "."):
            # 跳過__init__文件
            if modname.endswith("__init__"):
                continue
            module = importlib.import_module(modname)
            # 將 log 和 xiaomusic 注入模塊的命名空間
            # 這樣插件內部就可以直接使用 log 和 xiaomusic 對象
            module.log = self.log
            module.xiaomusic = self.xiaomusic

            # 動態獲取模塊中與文件名同名的函數
            # 約定：插件文件名即為入口函數名
            function_name = modname.split(".")[-1]  # 從模塊全名提取函數名
            if hasattr(module, function_name):
                self._funcs[function_name] = getattr(module, function_name)
            else:
                self.log.error(
                    f"No function named '{function_name}' found in module {modname}"
                )

    def get_func(self, plugin_name):
        """根據插件名獲取插件函數"""
        return self._funcs.get(plugin_name)

    def get_local_namespace(self):
        """返回包含所有插件函數的字典，可以用作 exec 要執行的代碼的命名空間"""
        return self._funcs.copy()

    async def execute_plugin(self, code):
        """
        執行指定的插件代碼。插件函數可以是同步或異步。
        :param code: 需要執行的插件函數代碼（例如 'plugin1("hello")'）
        """
        # 分解代碼字符串以獲取函數名
        func_name = code.split("(")[0]

        # 根據解析出的函數名從插件字典中獲取函數
        plugin_func = self.get_func(func_name)

        if not plugin_func:
            raise ValueError(f"No plugin function named '{func_name}' found.")

        # 檢查函數是否是異步函數
        global_namespace = globals().copy()
        local_namespace = self.get_local_namespace()
        if inspect.iscoroutinefunction(plugin_func):
            # 如果是異步函數，構建執行用的協程對象
            coroutine = eval(code, global_namespace, local_namespace)
            # 等待協程執行
            await coroutine
        else:
            # 如果是普通函數，直接執行代碼
            eval(code, global_namespace, local_namespace)
