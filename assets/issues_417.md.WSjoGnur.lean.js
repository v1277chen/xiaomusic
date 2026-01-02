import{_ as a,c as n,a0 as p,o as l}from"./chunks/framework.C8sLidy6.js";const h=JSON.parse('{"title":"Jellyfin歌单同步到映射目录","description":"","frontmatter":{"title":"Jellyfin歌单同步到映射目录"},"headers":[],"relativePath":"issues/417.md","filePath":"issues/417.md","lastUpdated":1741310830000}'),e={name:"issues/417.md"};function i(t,s,o,r,c,f){return l(),n("div",null,s[0]||(s[0]=[p(`<h1 id="jellyfin歌单同步到映射目录" tabindex="-1">Jellyfin歌单同步到映射目录 <a class="header-anchor" href="#jellyfin歌单同步到映射目录" aria-label="Permalink to &quot;Jellyfin歌单同步到映射目录&quot;" target="_self">​</a></h1><p>因为我一直是用jellyfin 听歌, 下载的都是在Pt网站 按照歌手几十张cd一起下载,所以有非常多不想听的歌,于是歌单功能非常重要,既然Jellyfin本上就创建了歌单,我就写一个Python 来同步这个歌单,大家放在 C:\\ProgramData\\Jellyfin\\Server\\data\\playlists 或者自定义的playlists文件夹中.</p><div class="language- vp-adaptive-theme"><button title="Copy Code" class="copy"></button><span class="lang"></span><pre class="shiki shiki-themes github-light github-dark vp-code" tabindex="0"><code><span class="line"><span>import os</span></span>
<span class="line"><span>import xml.etree.ElementTree as ET</span></span>
<span class="line"><span>import shutil</span></span>
<span class="line"><span></span></span>
<span class="line"><span>#这个文件要放在Jellyfin的C:\\ProgramData\\Jellyfin\\Server\\data\\playlists  中</span></span>
<span class="line"><span>#这里要改成想要硬链接到的目录地址</span></span>
<span class="line"><span>folder_path = r&#39;E:\\music2\\歌曲列表&#39;</span></span>
<span class="line"><span></span></span>
<span class="line"><span>def create_hard_links_from_xml(file_path):</span></span>
<span class="line"><span>     # 解析 XML 文件</span></span>
<span class="line"><span>    tree = ET.parse(file_path)</span></span>
<span class="line"><span>    root = tree.getroot()</span></span>
<span class="line"><span></span></span>
<span class="line"><span>    # 获取 LocalTitle 标签的值</span></span>
<span class="line"><span>    local_title = root.find(&#39;LocalTitle&#39;).text</span></span>
<span class="line"><span>    # 构建目标目录路径</span></span>
<span class="line"><span>    target_dir = os.path.join(folder_path, local_title)</span></span>
<span class="line"><span></span></span>
<span class="line"><span>    # 检查目标目录是否存在，如果不存在则创建</span></span>
<span class="line"><span>    if not os.path.exists(target_dir):</span></span>
<span class="line"><span>        os.makedirs(target_dir)</span></span>
<span class="line"><span></span></span>
<span class="line"><span>    # 遍历所有 PlaylistItem 节点</span></span>
<span class="line"><span>    for playlist_item in root.findall(&#39;.//PlaylistItem&#39;):</span></span>
<span class="line"><span>        # 获取 Path 标签的值</span></span>
<span class="line"><span>        file_path = playlist_item.find(&#39;Path&#39;).text</span></span>
<span class="line"><span>        if os.path.exists(file_path):</span></span>
<span class="line"><span>            # 获取文件名</span></span>
<span class="line"><span>            file_name = os.path.basename(file_path)</span></span>
<span class="line"><span>            target_file = os.path.join(target_dir, file_name)</span></span>
<span class="line"><span>            try:</span></span>
<span class="line"><span>                # 创建硬链接</span></span>
<span class="line"><span>                os.link(file_path, target_file)</span></span>
<span class="line"><span>                print(f&quot;成功为 {file_path} 创建硬链接到 {target_file}&quot;)</span></span>
<span class="line"><span>            except FileExistsError:</span></span>
<span class="line"><span>                print(f&quot;目标文件 {target_file} 已存在，跳过。&quot;)</span></span>
<span class="line"><span>            except Exception as e:</span></span>
<span class="line"><span>                print(f&quot;为 {file_path} 创建硬链接时出错: {e}&quot;)</span></span>
<span class="line"><span>        else:</span></span>
<span class="line"><span>            print(f&quot;源文件 {file_path} 不存在，跳过。&quot;)</span></span>
<span class="line"><span></span></span>
<span class="line"><span>shutil.rmtree(folder_path)</span></span>
<span class="line"><span></span></span>
<span class="line"><span># 检查文件夹是否存在</span></span>
<span class="line"><span>if not os.path.exists(folder_path):</span></span>
<span class="line"><span>    # 创建单层或多层文件夹</span></span>
<span class="line"><span>    os.makedirs(folder_path)</span></span>
<span class="line"><span>    print(f&quot;文件夹 &#39;{folder_path}&#39; 创建成功&quot;)</span></span>
<span class="line"><span></span></span>
<span class="line"><span>def print_xml_content():</span></span>
<span class="line"><span>    # 获取当前目录</span></span>
<span class="line"><span>    current_dir = os.getcwd()</span></span>
<span class="line"><span>    # 遍历当前目录及其所有子目录</span></span>
<span class="line"><span>    for root, dirs, files in os.walk(current_dir):</span></span>
<span class="line"><span>        for file in files:</span></span>
<span class="line"><span>            # 检查文件扩展名是否为 .xml</span></span>
<span class="line"><span>            if file.lower().endswith(&#39;.xml&#39;):</span></span>
<span class="line"><span>                file_path = os.path.join(root, file)</span></span>
<span class="line"><span>                create_hard_links_from_xml(file_path)</span></span>
<span class="line"><span></span></span>
<span class="line"><span>if __name__ == &quot;__main__&quot;:</span></span>
<span class="line"><span>    print_xml_content()</span></span></code></pre></div><h2 id="评论" tabindex="-1">评论 <a class="header-anchor" href="#评论" aria-label="Permalink to &quot;评论&quot;" target="_self">​</a></h2><h3 id="评论-1-colakot" tabindex="-1">评论 1 - colaKot <a class="header-anchor" href="#评论-1-colakot" aria-label="Permalink to &quot;评论 1 - colaKot&quot;" target="_self">​</a></h3><p>创建一个xmllink.py 文件 然后把代码放进去,双击运行就行,当然要装python</p><hr><p><a href="https://github.com/hanxi/xiaomusic/issues/417" target="_self">链接到 GitHub Issue</a></p>`,8)]))}const d=a(e,[["render",i]]);export{h as __pageData,d as default};
