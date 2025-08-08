import os
import re
import json
import arxiv
import yaml
import logging
import argparse
import datetime
import requests
import subprocess
import time

logging.basicConfig(format='[%(asctime)s %(levelname)s] %(message)s',
                    datefmt='%m/%d/%Y %H:%M:%S',
                    level=logging.INFO)

# ======= 常量（替换掉原来的 arxiv.paperswithcode base_url） =======
# Hugging Face：用 arxiv_id 映射 Hub 上的 spaces/models/datasets
HF_REPOS_API = "https://huggingface.co/api/arxiv/{arxiv_id}/repos"
HF_HEADERS = {"User-Agent": "arxiv-daily/1.0"}

# GitHub 搜索（兜底）
GITHUB_SEARCH_REPO = "https://api.github.com/search/repositories"
GITHUB_SEARCH_CODE = "https://api.github.com/search/code"
GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "arxiv-daily/1.0"
}
if os.getenv("GITHUB_TOKEN"):
    GH_HEADERS["Authorization"] = f"Bearer {os.getenv('GITHUB_TOKEN')}"

# arXiv 页面
arxiv_url = "https://arxiv.org/"

# ======= 工具函数 =======

def load_config(config_file:str) -> dict:
    '''
    config_file: input config file path
    return: a dict of configuration
    '''
    # make filters pretty
    def pretty_filters(**config) -> dict:
        keywords = dict()
        EXCAPE = '\"'
        QUOTA = '' # NO-USE
        OR = 'OR' # TODO
        def parse_filters(filters:list):
            ret = ''
            for idx in range(0,len(filters)):
                filter = filters[idx]
                if len(filter.split()) > 1:
                    ret += (EXCAPE + filter + EXCAPE)
                else:
                    ret += (QUOTA + filter + QUOTA)
                if idx != len(filters) - 1:
                    ret += OR
            return ret
        for k,v in config['keywords'].items():
            keywords[k] = parse_filters(v['filters'])
        return keywords
    with open(config_file,'r') as f:
        config = yaml.load(f,Loader=yaml.FullLoader)
        config['kv'] = pretty_filters(**config)
        logging.info(f'config = {config}')
    return config

def get_authors(authors, first_author = False):
    output = str()
    if first_author == False:
        output = ", ".join(str(author) for author in authors)
    else:
        output = str(authors[0])
    return output

def sort_papers(papers):
    output = dict()
    keys = list(papers.keys())
    keys.sort(reverse=True)
    for key in keys:
        output[key] = papers[key]
    return output

def http_get(url, headers=None, params=None, timeout=10, retries=2, sleep=0.8):
    """ 简单 GET 带重试 """
    last_exc = None
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=timeout)
            if r.status_code == 200:
                return r
            else:
                logging.warning(f"GET {url} status={r.status_code} params={params}")
        except Exception as e:
            last_exc = e
            logging.warning(f"GET {url} exception: {e}")
        time.sleep(sleep)
    if last_exc:
        raise last_exc
    return None

def get_code_link(qword:str) -> str:
    """
    用 GitHub 仓库搜索找一个可能的实现（按 stars 降序）。
    @param qword: 论文标题或 arxiv id
    @return 仓库 html_url 或 None
    """
    params = {
        "q": qword,
        "sort": "stars",
        "order": "desc",
        "per_page": 5
    }
    try:
        r = http_get(GITHUB_SEARCH_REPO, headers=GH_HEADERS, params=params, timeout=10)
        if not r:
            return None
        results = r.json()
        items = results.get("items", [])
        if items:
            return items[0].get("html_url")
    except Exception as e:
        logging.error(f"GitHub search error: {e}")
    return None

def find_code_repo(paper_title, arxiv_id_no_ver, primary_author=None):
    """
    比 get_code_link 更智能一些：
    1) 用标题短语搜 README/描述
    2) 再用 arXiv ID 搜
    3) 再用 Code Search 在 README 文件里搜 arXiv ID
    """
    try:
        # 1) 标题短语搜索
        q1 = f"\"{paper_title}\" in:readme,in:description"
        r = http_get(GITHUB_SEARCH_REPO, headers=GH_HEADERS,
                     params={"q": q1, "sort": "stars", "order": "desc", "per_page": 5}, timeout=10)
        if r and r.json().get("items"):
            return r.json()["items"][0]["html_url"]

        # 2) arXiv ID 搜索
        q2 = f"\"{arxiv_id_no_ver}\" in:name,readme,description"
        r = http_get(GITHUB_SEARCH_REPO, headers=GH_HEADERS,
                     params={"q": q2, "sort": "stars", "order": "desc", "per_page": 5}, timeout=10)
        if r and r.json().get("items"):
            return r.json()["items"][0]["html_url"]

        # 3) Code Search：README 中包含 arXiv ID
        q3 = f"\"{arxiv_id_no_ver}\" in:file filename:README"
        r = http_get(GITHUB_SEARCH_CODE, headers=GH_HEADERS,
                     params={"q": q3, "per_page": 5}, timeout=10)
        if r and r.json().get("items"):
            return r.json()["items"][0]["repository"]["html_url"]
    except Exception as e:
        logging.error(f"find_code_repo error: {e}")
    return None

def get_repo_from_hf(arxiv_id_no_ver):
    """
    从 Hugging Face Hub 获取与论文关联的 spaces/models/datasets。
    优先选择：Spaces -> Models -> Datasets
    返回对应的 Hub 链接，失败返回 None。
    """
    url = HF_REPOS_API.format(arxiv_id=arxiv_id_no_ver)
    try:
        r = http_get(url, headers=HF_HEADERS, timeout=10)
        if not r:
            return None
        data = r.json()  # {"models":[...], "datasets":[...], "spaces":[...]}

        def pick(arr, t):
            for it in (arr or []):
                rid = it.get("id")  # 形如 "org/name"
                if rid:
                    return f"https://huggingface.co/{t}/{rid}"
            return None

        return (pick(data.get("spaces"), "spaces")
                or pick(data.get("models"), "models")
                or pick(data.get("datasets"), "datasets"))
    except Exception as e:
        logging.error(f"HF repos error: {e}")
        return None

def get_daily_papers(topic,query="slam", max_results=2):
    """
    @param topic: str
    @param query: str
    @return paper_with_code: dict
    """
    content = dict()
    content_to_web = dict()

    search_engine = arxiv.Search(
        query = query,
        max_results = max_results,
        sort_by = arxiv.SortCriterion.SubmittedDate
    )

    for result in search_engine.results():

        paper_id            = result.get_short_id()         # 例如 2108.09112v1
        paper_title         = result.title
        paper_url           = result.entry_id
        paper_abstract      = result.summary.replace("\n"," ")
        paper_authors       = get_authors(result.authors)
        paper_first_author  = get_authors(result.authors,first_author = True)
        primary_category    = result.primary_category
        publish_time        = result.published.date()
        update_time         = result.updated.date()
        comments            = result.comment

        logging.info(f"Time = {update_time} title = {paper_title} author = {paper_first_author}")

        # 去掉版本号：2108.09112v1 -> 2108.09112
        ver_pos = paper_id.find('v')
        paper_key = paper_id if ver_pos == -1 else paper_id[:ver_pos]
        paper_url = arxiv_url + 'abs/' + paper_key

        # 先尝试 HF，失败再 GitHub 搜索作为兜底
        repo_url = get_repo_from_hf(paper_key)
        if repo_url is None:
            repo_url = find_code_repo(paper_title, paper_key, paper_first_author) \
                       or get_code_link(paper_title) \
                       or get_code_link(paper_key)

        try:
            if repo_url is not None:
                content[paper_key] = "|**{}**|**{}**|{} et.al.|[{}]({})|**[link]({})**|\n".format(
                       update_time,paper_title,paper_first_author,paper_key,paper_url,repo_url)
                content_to_web[paper_key] = "- {}, **{}**, {} et.al., Paper: [{}]({}), Code: **[{}]({})**".format(
                       update_time,paper_title,paper_first_author,paper_url,paper_url,repo_url,repo_url)
            else:
                content[paper_key] = "|**{}**|**{}**|{} et.al.|[{}]({})|null|\n".format(
                       update_time,paper_title,paper_first_author,paper_key,paper_url)
                content_to_web[paper_key] = "- {}, **{}**, {} et.al., Paper: [{}]({})".format(
                       update_time,paper_title,paper_first_author,paper_url,paper_url)

            comments = None  # TODO: 保留注释逻辑
            if comments != None:
                content_to_web[paper_key] += f", {comments}\n"
            else:
                content_to_web[paper_key] += f"\n"

        except Exception as e:
            logging.error(f"exception: {e} with id: {paper_key}")

    data = {topic:content}
    data_web = {topic:content_to_web}
    return data,data_web

def update_paper_links(filename):
    '''
    weekly update paper links in json file
    '''
    def parse_arxiv_string(s):
        parts = s.split("|")
        date = parts[1].strip()
        title = parts[2].strip()
        authors = parts[3].strip()
        arxiv_id = parts[4].strip()
        code = parts[5].strip()
        arxiv_id = re.sub(r'v\d+', '', arxiv_id)
        return date,title,authors,arxiv_id,code

    with open(filename,"r") as f:
        content = f.read()
        if not content:
            m = {}
        else:
            m = json.loads(content)

        json_data = m.copy()

        for keywords,v in json_data.items():
            logging.info(f'keywords = {keywords}')
            for paper_id,contents in v.items():
                contents = str(contents)

                update_time, paper_title, paper_first_author, paper_url_field, code_url = parse_arxiv_string(contents)

                # 保持原格式
                contents = "|{}|{}|{}|{}|{}|\n".format(update_time,paper_title,paper_first_author,paper_url_field,code_url)
                json_data[keywords][paper_id] = str(contents)
                logging.info(f'paper_id = {paper_id}, contents = {contents}')

                valid_link = False if '|null|' in contents else True
                if valid_link:
                    continue
                try:
                    # 先 HF，再 GitHub（注意这里能拿到标题和一作）
                    repo_url = get_repo_from_hf(paper_id) \
                               or find_code_repo(paper_title, paper_id, paper_first_author) \
                               or get_code_link(paper_title) \
                               or get_code_link(paper_id)

                    if repo_url is not None:
                        new_cont = contents.replace('|null|',f'|**[link]({repo_url})**|')
                        logging.info(f'ID = {paper_id}, contents = {new_cont}')
                        json_data[keywords][paper_id] = str(new_cont)

                except Exception as e:
                    logging.error(f"exception: {e} with id: {paper_id}")
        # dump to json file
        with open(filename,"w") as f:
            json.dump(json_data,f)

def update_json_file(filename,data_dict):
    '''
    daily update json file using data_dict
    '''
    with open(filename,"r") as f:
        content = f.read()
        if not content:
            m = {}
        else:
            m = json.loads(content)

    json_data = m.copy()

    # update papers in each keywords
    for data in data_dict:
        for keyword in data.keys():
            papers = data[keyword]

            if keyword in json_data.keys():
                json_data[keyword].update(papers)
            else:
                json_data[keyword] = papers

    with open(filename,"w") as f:
        json.dump(json_data,f)

def json_to_md(filename,md_filename,
               task = '',
               to_web = False,
               use_title = True,
               use_tc = True,
               show_badge = True,
               use_b2t = True):
    """
    @param filename: str
    @param md_filename: str
    @return None
    """
    def pretty_math(s:str) -> str:
        ret = ''
        match = re.search(r"\$.*\$", s)
        if match == None:
            return s
        math_start,math_end = match.span()
        space_trail = space_leading = ''
        if s[:math_start][-1] != ' ' and '*' != s[:math_start][-1]: space_trail = ' '
        if s[math_end:][0] != ' ' and '*' != s[math_end:][0]: space_leading = ' '
        ret += s[:math_start]
        ret += f'{space_trail}${match.group()[1:-1].strip()}${space_leading}'
        ret += s[math_end:]
        return ret

    DateNow = datetime.date.today()
    DateNow = str(DateNow)
    DateNow = DateNow.replace('-','.')

    with open(filename,"r") as f:
        content = f.read()
        if not content:
            data = {}
        else:
            data = json.loads(content)

    # clean README.md if daily already exist else create it
    with open(md_filename,"w+") as f:
        pass

    # write data into README.md
    with open(md_filename,"a+") as f:

        if (use_title == True) and (to_web == True):
            f.write("---\n" + "layout: default\n" + "---\n\n")

        if show_badge == True:
            f.write(f"[![Contributors][contributors-shield]][contributors-url]\n")
            f.write(f"[![Forks][forks-shield]][forks-url]\n")
            f.write(f"[![Stargazers][stars-shield]][stars-url]\n")
            f.write(f"[![Issues][issues-shield]][issues-url]\n\n")

        if use_title == True:
            f.write("## Updated on " + DateNow + "\n")
        else:
            f.write("> Updated on " + DateNow + "\n")

        f.write("> Usage instructions: [here](./docs/README.md#usage)\n\n")

        #Add: table of contents
        if use_tc == True:
            f.write("<details>\n")
            f.write("  <summary>Table of Contents</summary>\n")
            f.write("  <ol>\n")
            for keyword in data.keys():
                day_content = data[keyword]
                if not day_content:
                    continue
                kw = keyword.replace(' ','-')
                f.write(f"    <li><a href=#{kw.lower()}>{keyword}</a></li>\n")
            f.write("  </ol>\n")
            f.write("</details>\n\n")

        for keyword in data.keys():
            day_content = data[keyword]
            if not day_content:
                continue
            # the head of each part
            f.write(f"## {keyword}\n\n")

            if use_title == True :
                if to_web == False:
                    f.write("|Publish Date|Title|Authors|PDF|Code|\n" + "|---|---|---|---|---|\n")
                else:
                    f.write("| Publish Date | Title | Authors | PDF | Code |\n")
                    f.write("|:---------|:-----------------------|:---------|:------|:------|\n")

            # sort papers by date
            day_content = sort_papers(day_content)

            for _,v in day_content.items():
                if v is not None:
                    f.write(pretty_math(v)) # make latex pretty

            f.write(f"\n")

            #Add: back to top
            if use_b2t:
                top_info = f"#Updated on {DateNow}"
                top_info = top_info.replace(' ','-').replace('.','')
                f.write(f"<p align=right>(<a href={top_info.lower()}>back to top</a>)</p>\n\n")

        if show_badge == True:
            # we don't like long string, break it!
            f.write((f"[contributors-shield]: https://img.shields.io/github/"
                     f"contributors/Vincentqyw/cv-arxiv-daily.svg?style=for-the-badge\n"))
            f.write((f"[contributors-url]: https://github.com/Vincentqyw/"
                     f"cv-arxiv-daily/graphs/contributors\n"))
            f.write((f"[forks-shield]: https://img.shields.io/github/forks/Vincentqyw/"
                     f"cv-arxiv-daily.svg?style=for-the-badge\n"))
            f.write((f"[forks-url]: https://github.com/Vincentqyw/"
                     f"cv-arxiv-daily/network/members\n"))
            f.write((f"[stars-shield]: https://img.shields.io/github/stars/Vincentqyw/"
                     f"cv-arxiv-daily.svg?style=for-the-badge\n"))
            f.write((f"[stars-url]: https://github.com/Vincentqyw/"
                     f"cv-arxiv-daily/stargazers\n"))
            f.write((f"[issues-shield]: https://img.shields.io/github/issues/Vincentqyw/"
                     f"cv-arxiv-daily.svg?style=for-the-badge\n"))
            f.write((f"[issues-url]: https://github.com/Vincentqyw/"
                     f"cv-arxiv-daily/issues\n\n"))

    logging.info(f"{task} finished")

def demo(**config):
    # TODO: use config
    data_collector = []
    data_collector_web= []

    keywords = config['kv']
    max_results = config['max_results']
    publish_readme = config['publish_readme']
    publish_gitpage = config['publish_gitpage']
    publish_wechat = config['publish_wechat']
    show_badge = config['show_badge']

    b_update = config['update_paper_links']
    logging.info(f'Update Paper Link = {b_update}')
    if config['update_paper_links'] == False:
        logging.info(f"GET daily papers begin")
        for topic, keyword in keywords.items():
            logging.info(f"Keyword: {topic}")
            data, data_web = get_daily_papers(topic, query = keyword,
                                            max_results = max_results)
            data_collector.append(data)
            data_collector_web.append(data_web)
            print("\n")
        logging.info(f"GET daily papers end")

    # 1. update README.md file
    if publish_readme:
        json_file = config['json_readme_path']
        md_file   = config['md_readme_path']
        # update paper links
        if config['update_paper_links']:
            update_paper_links(json_file)
        else:
            # update json data
            update_json_file(json_file,data_collector)
        # json data to markdown
        json_to_md(json_file,md_file, task ='Update Readme', \
            show_badge = show_badge)

    # 2. update docs/index.md file (to gitpage)
    if publish_gitpage:
        json_file = config['json_gitpage_path']
        md_file   = config['md_gitpage_path']
        # TODO: duplicated update paper links!!!
        if config['update_paper_links']:
            update_paper_links(json_file)
        else:
            update_json_file(json_file,data_collector)
        json_to_md(json_file, md_file, task ='Update GitPage', \
            to_web = True, show_badge = show_badge, \
            use_tc=False, use_b2t=False)

    # 3. Update docs/wechat.md file
    if publish_wechat:
        json_file = config['json_wechat_path']
        md_file   = config['md_wechat_path']
        # TODO: duplicated update paper links!!!
        if config['update_paper_links']:
            update_paper_links(json_file)
        else:
            update_json_file(json_file, data_collector_web)
        json_to_md(json_file, md_file, task ='Update Wechat', \
            to_web=False, use_title= False, show_badge = show_badge)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--config_path',type=str, default='config.yaml',
                            help='configuration file path')
    parser.add_argument('--update_paper_links', default=False,
                        action="store_true",help='whether to update paper links etc.')
    args = parser.parse_args()
    config = load_config(args.config_path)
    config = {**config, 'update_paper_links':args.update_paper_links}
    demo(**config)

    try:
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(["git", "commit", "-m", "commit"], check=True)
        subprocess.run(["git", "push", "-u", "origin", "main"], check=True)
        print("Git commands executed successfully.")
    except subprocess.CalledProcessError as e:
        pass
