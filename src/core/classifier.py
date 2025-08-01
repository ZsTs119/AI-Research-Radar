"""
智能分类器模块
负责对论文进行智能分类并生成MD文件和汇总报告
"""
import os
import re
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from ..utils.console import ConsoleOutput
from ..utils.logger import get_logger
from ..utils.file_utils import FileManager
from ..utils.progress import ProgressManager
from ..utils.ai_client import create_retryable_client
from ..models.report import AnalysisResult, ClassificationResult, AnalysisSummary


class PaperClassifier:
    """
    论文智能分类器
    
    负责对分析结果进行智能分类，生成分类MD文件和汇总报告
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        初始化分类器
        
        Args:
            config: 配置字典
        """
        self.config = config
        self.console = ConsoleOutput()
        self.logger = get_logger('classifier')
        self.file_manager = FileManager('classifier')
        
        # 设置默认配置
        self.output_dir = config.get('output_dir', 'data/analysis_results')
        self.ai_model = config.get('ai_model', 'zhipu')
        self.use_ai = config.get('use_ai', True)
        self.knowledge_file = config.get('knowledge_file', '模型分类.md')
        self.delay_between_requests = config.get('delay_between_requests', 1)
        
        # 初始化AI客户端
        self.ai_client = None
        if self.use_ai:
            try:
                self.ai_client = create_retryable_client(
                    self.ai_model,
                    max_retries=3
                )
            except Exception as e:
                self.logger.warning(f"AI客户端初始化失败: {e}")
                self.use_ai = False
        
        # 加载知识库
        self.knowledge_base = self._load_knowledge_base()
    
    def _load_knowledge_base(self) -> str:
        """
        加载分类知识库
        
        Returns:
            知识库内容
        """
        if not os.path.exists(self.knowledge_file):
            self.logger.warning(f"知识库文件不存在: {self.knowledge_file}")
            return ""
        
        try:
            with open(self.knowledge_file, 'r', encoding='utf-8') as f:
                content = f.read()
            self.logger.info(f"成功加载知识库: {self.knowledge_file}")
            return content
        except Exception as e:
            self.logger.error(f"加载知识库失败: {e}")
            return ""
    
    def split_to_md(self, analysis_results: List[AnalysisResult],
                   date: str, silent: bool = False) -> bool:
        """
        步骤1：切分JSON为单个MD文件（类似旧脚本）

        Args:
            analysis_results: 分析结果列表
            date: 日期字符串
            silent: 是否静默模式

        Returns:
            是否成功
        """
        if not analysis_results:
            if not silent:
                self.console.print_warning("没有论文需要切分")
            return False

        if not silent:
            self.console.print_info(f"开始切分 {len(analysis_results)} 篇论文为MD文件")

        self.logger.info(f"开始MD切分 {len(analysis_results)} 篇论文")

        try:
            # 创建日期目录
            date_dir = Path(self.output_dir) / date
            date_dir.mkdir(parents=True, exist_ok=True)

            # 为每篇论文创建MD文件
            for i, analysis_result in enumerate(analysis_results):
                if not silent:
                    # 显示当前处理的论文信息（类似cleaner的体验）
                    self.console.print_info(f"🔍 切分第 {i+1}/{len(analysis_results)} 篇: {analysis_result.translation[:50]}...")

                    # 显示整体进度条
                    progress_bar = self._create_progress_bar(i, len(analysis_results))
                    print(f"✂️ MD切分进度: {progress_bar} {i}/{len(analysis_results)}")

                # 生成安全的文件名
                safe_title = "".join(c for c in analysis_result.translation if c.isalnum() or c in (' ', '-', '_')).rstrip()
                safe_title = safe_title[:50]  # 限制长度
                if not safe_title:
                    safe_title = f"paper_{analysis_result.paper_id}"

                md_filename = f"{safe_title}.md"
                md_path = date_dir / md_filename

                # 生成MD内容
                content = f"""# {analysis_result.translation}

**论文标题**：{analysis_result.title}
**中文标题**：{analysis_result.translation}
**论文地址**：{analysis_result.paper_url}

**作者团队**：{analysis_result.authors}
**发表日期**：{analysis_result.publish_date}
**模型功能**：{analysis_result.model_function}
"""

                # 写入MD文件
                with open(md_path, 'w', encoding='utf-8') as f:
                    f.write(content)

                if not silent:
                    self.console.print_success(f"✅ 切分完成: {md_filename}")

                self.logger.info(f"MD文件创建成功: {md_path}")

                # 添加延迟（类似cleaner的体验）
                if i < len(analysis_results) - 1:  # 最后一个不需要延迟
                    import time
                    time.sleep(0.1)  # 短暂延迟，让用户看到进度

            if not silent:
                self.console.print_success(f"📁 MD切分完成，输出目录: {date_dir}")

            return True

        except Exception as e:
            if not silent:
                self.console.print_error(f"MD切分失败: {e}")
            self.logger.error(f"MD切分异常: {e}")
            return False

    def classify_papers(self, analysis_results: List[AnalysisResult],
                       date: str = None, silent: bool = False) -> List[ClassificationResult]:
        """
        批量分类论文

        Args:
            analysis_results: 分析结果列表
            date: 日期字符串
            silent: 是否静默模式

        Returns:
            分类结果列表
        """
        if not analysis_results:
            if not silent:
                self.console.print_warning("没有论文需要分类")
            return []

        if not silent:
            self.console.print_info(f"开始分类 {len(analysis_results)} 篇论文")
        
        self.logger.info(f"开始批量分类 {len(analysis_results)} 篇论文")
        
        # 初始化进度管理器
        progress = ProgressManager(len(analysis_results), "智能分类论文") if not silent else None
        results = []

        # 统计变量
        processed_count = 0
        success_count = 0
        fail_count = 0
        skip_count = 0
        
        # 顺序处理每篇论文
        for i, analysis_result in enumerate(analysis_results):
            if not silent:
                # 显示当前处理的论文信息（类似基础脚本）
                self.console.print_info(f"🔍 处理第 {i+1}/{len(analysis_results)} 篇: {analysis_result.translation[:50]}...")

                # 显示整体进度条（类似基础脚本）
                progress_bar = self._create_progress_bar(i, len(analysis_results))
                remaining_papers = len(analysis_results) - i - 1
                estimated_remaining = remaining_papers * 20  # 假设每篇20秒
                print(f"📊 进度: {progress_bar} {i}/{len(analysis_results)} (成功:{success_count}, 失败:{fail_count}, 跳过:{skip_count}) 预计剩余: {estimated_remaining}秒")

            self.logger.info(f"开始分类论文: {analysis_result.paper_id}")

            try:
                # 分类单篇论文并立即保存MD文件（类似旧脚本）
                result = self.classify_and_save_single_paper(analysis_result, date, silent=silent)

                if result:
                    # 检查是否是跳过的论文（通过confidence和md_content判断）
                    if result.confidence == 1.0 and result.md_content == "":
                        skip_count += 1
                    else:
                        results.append(result)
                        success_count += 1
                        processed_count += 1

                        if progress:
                            progress.update(True, f"{analysis_result.paper_id}")

                        if not silent:
                            # 显示成功信息（类似旧脚本）
                            self.console.print_success(f"✅ 分类完成: {result.category} - {analysis_result.paper_id}")

                        self.logger.info(f"论文分类完成: {analysis_result.paper_id} -> {result.category}")
                else:
                    fail_count += 1
                    processed_count += 1

                    if progress:
                        progress.update(False, f"{analysis_result.paper_id}")

                    if not silent:
                        self.console.print_error(f"❌ 分类失败: {analysis_result.paper_id}")

                    self.logger.error(f"论文分类失败: {analysis_result.paper_id}")

                # 添加延迟避免API限制（与旧脚本一致）
                import time
                time.sleep(1)
                    
            except Exception as e:
                fail_count += 1
                processed_count += 1

                if progress:
                    progress.update(False, f"{analysis_result.paper_id} - {e}")

                if not silent:
                    self.console.print_error(f"❌ 异常: {analysis_result.paper_id} - {e}")

                self.logger.error(f"论文分类异常: {analysis_result.paper_id} - {e}")
        
        # 显示最终统计
        if progress:
            progress.finish()

        if not silent:
            # 计算实际处理的论文数（排除跳过的）
            actually_processed = processed_count

            self.console.print_summary("分类完成统计", {
                "总论文数": len(analysis_results),
                "跳过论文": skip_count,
                "实际处理": actually_processed,
                "成功分类": success_count,
                "分类失败": fail_count,
                "成功率": f"{success_count/max(actually_processed, 1)*100:.1f}%" if actually_processed > 0 else "0.0%"
            })

        self.logger.info(f"批量分类完成，成功: {success_count}/{actually_processed}，跳过: {skip_count}")
        return results
    
    def classify_single_paper(self, analysis_result: AnalysisResult, 
                             silent: bool = False) -> Optional[ClassificationResult]:
        """
        分类单篇论文
        
        Args:
            analysis_result: 分析结果
            silent: 是否静默模式
            
        Returns:
            分类结果，失败返回None
        """
        if not self.use_ai or not self.ai_client:
            if not silent:
                self.console.print_warning("AI分类未启用，返回默认分类")
            
            # 返回默认分类结果
            return ClassificationResult(
                paper_id=analysis_result.paper_id,
                category="多模态生成",
                confidence=0.5,
                md_content=self._generate_default_md_content(analysis_result)
            )
        
        try:
            # 构建分类提示词
            prompt = self._build_classification_prompt(analysis_result)

            messages = [
                {
                    "role": "user",
                    "content": [{
                        "type": "text",
                        "text": prompt
                    }]
                }
            ]

            # AI调用（带实时进度显示）
            if not silent:
                import threading
                import time

                # 创建进度显示线程
                progress_stop = threading.Event()
                progress_thread = threading.Thread(
                    target=self._show_classification_progress,
                    args=(progress_stop, f"分类论文: {analysis_result.translation[:30]}...")
                )
                progress_thread.daemon = True
                progress_thread.start()

            start_time = time.time()

            try:
                # 直接调用AI
                response = self.ai_client.chat(messages)
            finally:
                if not silent:
                    progress_stop.set()
                    progress_thread.join(timeout=1)
                    print()  # 换行

            if not silent:
                end_time = time.time()
                self.console.print_info(f"AI响应耗时: {end_time - start_time:.2f}秒")

            if not response:
                self.logger.error(f"AI分类失败，响应为空: {analysis_result.paper_id}")
                return None

        except Exception as e:
            self.logger.error(f"AI分类异常: {analysis_result.paper_id} - {e}")
            return None

        # 处理AI响应
        try:
            
            if response:
                # 解析AI响应
                category, confidence, md_content = self._parse_classification_response(response)
                
                # 创建分类结果
                result = ClassificationResult(
                    paper_id=analysis_result.paper_id,
                    category=category,
                    confidence=confidence,
                    md_content=md_content
                )
                
                return result
            else:
                self.logger.error(f"AI分类返回空结果: {analysis_result.paper_id}")
                return None
                
        except Exception as e:
            self.logger.error(f"分类论文异常: {analysis_result.paper_id} - {e}")
            return None

    def classify_and_save_single_paper(self, analysis_result: AnalysisResult,
                                     date: str, silent: bool = False) -> Optional[ClassificationResult]:
        """
        分类单篇论文并立即保存MD文件（类似旧脚本）

        Args:
            analysis_result: 分析结果
            date: 日期字符串
            silent: 是否静默模式

        Returns:
            分类结果，失败返回None
        """
        # 生成原始MD文件名（与步骤1切分时一致）
        safe_title = "".join(c for c in analysis_result.translation if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe_title = safe_title[:50]  # 限制长度
        if not safe_title:
            safe_title = f"paper_{analysis_result.paper_id}"

        original_md_filename = f"{safe_title}.md"

        # 查找是否已存在分类文件
        date_dir = Path(self.output_dir) / date
        if date_dir.exists():
            for category_dir in date_dir.iterdir():
                if category_dir.is_dir():
                    expected_file = category_dir / original_md_filename
                    if expected_file.exists():
                        if not silent:
                            self.console.print_skip(f"已处理的论文: {analysis_result.paper_id}")

                        # 返回已存在的分类结果
                        return ClassificationResult(
                            paper_id=analysis_result.paper_id,
                            category=category_dir.name,
                            confidence=1.0,
                            md_content=""
                        )

        # 执行分类
        result = self.classify_single_paper(analysis_result, silent)

        if result:
            # 立即保存MD文件到分类目录（类似旧脚本）
            try:
                # 创建分类目录
                category_dir = date_dir / result.category
                category_dir.mkdir(parents=True, exist_ok=True)

                # 使用原始MD文件名（与旧脚本一致）
                md_filename = original_md_filename
                md_path = category_dir / md_filename

                # 写入分类后的MD文件
                with open(md_path, 'w', encoding='utf-8') as f:
                    f.write(result.md_content)

                if not silent:
                    self.console.print_success(f"✅ 分类完成: {result.category} - {md_filename}")

                self.logger.info(f"MD文件保存成功: {md_path}")

            except Exception as e:
                if not silent:
                    self.console.print_error(f"MD文件保存失败: {e}")
                self.logger.error(f"MD文件保存异常: {analysis_result.paper_id} - {e}")

        return result

    def _build_classification_prompt(self, analysis_result: AnalysisResult) -> str:
        """
        构建分类提示词
        
        Args:
            analysis_result: 分析结果
            
        Returns:
            提示词字符串
        """
        # 生成MD内容用于分类
        md_content = f"""# {analysis_result.translation}

**论文标题**：{analysis_result.title}
**中文标题**：{analysis_result.translation}
**论文地址**：{analysis_result.paper_url}

**作者团队**：{analysis_result.authors}
**发表日期**：{analysis_result.publish_date}
**模型功能**：{analysis_result.model_function}
"""
        
        prompt = f"""你是一个AI模型分类与总结专家。请根据下面的"模型分类知识库"，判断md文件描述的模型属于哪个分类，并按指定格式输出。

信息获取策略：
1. 优先使用md文件中已有的信息
2. 如果md文件中某些字段缺失或标注为"[未在md文件中提供]"、"[未提及]"等，请访问md文件中的arXiv链接获取完整信息
3. 确保所有字段都有准确、完整的内容

输出格式：
# [分类名称]

# [模型名称] - [论文标题]

**arXiv 文章链接**：[论文链接，格式：https://arxiv.org/abs/XXXX.XXXXX]

**作者/团队**：[作者姓名或机构名称。如果md文件中未提供，请访问arXiv链接获取作者信息]

**发表日期**：[YYYY-MM-DD格式。如果md文件中未提供，请访问arXiv链接获取论文提交日期]

**模型功能**：[基于论文内容，用1-2句话简洁描述模型的核心功能。如果md文件描述不够详细，请访问arXiv链接获取准确信息直接使用md文件中的模型功能信息]

**技术特点**：[用2-3句话总结模型的主要技术创新点，50字以内]

**应用场景**：[列举2-3个具体的应用场景。如果md文件信息不足，请访问arXiv链接获取更准确的应用场景]

分类规则：
- 必须从以下分类中选择：文本生成、音频生成、图像生成、视频生成、多模态生成、3D生成、游戏与策略生成、科学计算与数据生成、代码生成与数据增强、跨模态生成
- 如果不确定，选择"多模态生成"
- 如果模型涉及多个领域，选择最主要的功能分类

注意事项：
- 分类名称必须完全匹配知识库中的分类
- 所有字段都必须填写完整，不能留空或使用"[未提供]"等占位符
- 如果md文件信息不完整，务必访问arXiv链接补充信息
- 技术创新要突出与现有方法的区别
- 应用场景要具体可行

信息补充要求：
当遇到以下情况时，请访问arXiv链接获取信息：
- 作者/团队字段为空或标注"[未在md文件中提供]"
- 发表日期字段为空或标注"[未在md文件中提供]"
- 模型功能描述过于简单（少于20字）
- 技术创新内容重复或不够详细
- 应用场景过于泛泛而谈

模型分类知识库：
{self.knowledge_base}

md文件内容：
{md_content}"""
        
        return prompt

    def _create_progress_bar(self, current, total, width=50):
        """
        创建进度条（类似旧脚本的tqdm风格）

        Args:
            current: 当前进度
            total: 总数
            width: 进度条宽度

        Returns:
            进度条字符串
        """
        if total == 0:
            return "[" + "░" * width + "]"

        progress = current / total
        filled = int(width * progress)
        bar = "█" * filled + "░" * (width - filled)
        return f"[{bar}]"

    def _show_classification_progress(self, stop_event, task_name):
        """
        显示AI分类进度动画（实时预览时间）

        Args:
            stop_event: 停止事件
            task_name: 任务名称
        """
        import sys
        import time

        spinner = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
        start_time = time.time()
        i = 0
        warning_shown = False

        while not stop_event.is_set():
            elapsed = int(time.time() - start_time)
            minutes, seconds = divmod(elapsed, 60)
            time_str = f"{minutes:02d}:{seconds:02d}"

            # 在60秒时显示警告
            if elapsed >= 60 and not warning_shown:
                sys.stdout.write(f'\r🏷️ {task_name} {spinner[i % len(spinner)]} 已耗时: {time_str} ⚠️ 响应较慢...')
                warning_shown = True
            else:
                sys.stdout.write(f'\r🏷️ {task_name} {spinner[i % len(spinner)]} 已耗时: {time_str}')

            sys.stdout.flush()

            time.sleep(0.1)
            i += 1



    def generate_summary_report(self, date: str, silent: bool = False) -> bool:
        """
        生成分类汇总报告（类似旧脚本功能）

        Args:
            date: 日期字符串
            silent: 是否静默模式

        Returns:
            是否成功
        """
        try:
            output_dir = Path(self.output_dir) / date

            if not output_dir.exists():
                if not silent:
                    self.console.print_warning(f"分类目录不存在: {output_dir}")
                return False

            # 统计各分类的论文数量
            categories = {}

            for item in output_dir.iterdir():
                if item.is_dir() and not item.name.startswith('.'):
                    # 统计该分类下的MD文件数量
                    md_count = len([f for f in item.iterdir() if f.suffix == '.md'])
                    if md_count > 0:
                        categories[item.name] = md_count

            if not categories:
                if not silent:
                    self.console.print_warning("未找到任何分类结果")
                return False

            # 生成汇总报告内容
            total_papers = sum(categories.values())

            summary_content = f"# 论文分类汇总报告\n\n"
            summary_content += f"生成时间：{date}\n\n"
            summary_content += f"## 分类统计\n\n"
            summary_content += f"- **总论文数**：{total_papers} 篇\n"
            summary_content += f"- **分类数量**：{len(categories)} 个\n\n"
            summary_content += f"## 各分类详情\n\n"

            # 按论文数量降序排列
            for category, count in sorted(categories.items(), key=lambda x: x[1], reverse=True):
                percentage = count / total_papers * 100
                summary_content += f"### {category}\n"
                summary_content += f"- 论文数量：{count} 篇\n"
                summary_content += f"- 占比：{percentage:.1f}%\n\n"

            # 保存汇总报告
            summary_file = output_dir / "模型分类汇总.md"

            with open(summary_file, 'w', encoding='utf-8') as f:
                f.write(summary_content)

            if not silent:
                self.console.print_info(f"📊 汇总统计:")
                self.console.print_info(f"   - 总论文数: {total_papers} 篇")
                self.console.print_info(f"   - 分类数量: {len(categories)} 个")
                for category, count in sorted(categories.items(), key=lambda x: x[1], reverse=True):
                    percentage = count / total_papers * 100
                    self.console.print_info(f"   - {category}: {count} 篇 ({percentage:.1f}%)")

            self.logger.info(f"汇总报告生成成功: {summary_file}")
            return True

        except Exception as e:
            if not silent:
                self.console.print_error(f"汇总报告生成失败: {e}")
            self.logger.error(f"汇总报告生成异常: {e}")
            return False
    
    def _parse_classification_response(self, response: str) -> Tuple[str, float, str]:
        """
        解析分类响应（与旧脚本逻辑一致）

        Args:
            response: AI响应内容

        Returns:
            (分类名称, 置信度, MD内容)
        """
        category = "多模态生成"  # 默认分类
        confidence = 0.8  # 默认置信度
        md_content = ""

        try:
            # 按行分割响应（与旧脚本一致）
            lines = response.split('\n')

            # 提取分类名称（第一行，与旧脚本一致）
            if lines:
                category = lines[0].strip().replace('#', '').replace('：', '').replace(':', '').strip()
                # 去除可能的空行，获取实际的MD内容
                md_content = '\n'.join(lines[1:]).strip()

            # 如果分类名称为空，使用默认值
            if not category:
                category = "多模态生成"

            # 如果MD内容为空，生成默认内容
            if not md_content:
                md_content = f"# 模型分析\n\n**分类**：{category}\n\n**说明**：AI分析生成的内容"

        except Exception as e:
            self.logger.warning(f"解析分类响应异常: {e}")
            # 使用整个响应作为MD内容
            md_content = response.strip() if response else "# 分析失败\n\n无法解析AI响应"

        return category, confidence, md_content
    
    def _generate_default_md_content(self, analysis_result: AnalysisResult) -> str:
        """
        生成默认MD内容
        
        Args:
            analysis_result: 分析结果
            
        Returns:
            默认MD内容
        """
        return f"""# {analysis_result.translation}

**arXiv 文章链接**：{analysis_result.paper_url}

**作者/团队**：{analysis_result.authors or '未提供'}

**发表日期**：{analysis_result.publish_date or '未提供'}

**模型功能**：{analysis_result.model_function or '未提供'}

**技术特点**：基于论文内容的技术创新

**应用场景**：多种实际应用场景"""
    

    
    def save_classification_results(self, date: str,
                                  classification_results: List[ClassificationResult]) -> bool:
        """
        保存分类结果到文件

        注意：MD文件已经在classify_and_save_single_paper方法中保存了，
        这里只需要保存分类统计信息或其他汇总数据

        Args:
            date: 日期字符串
            classification_results: 分类结果列表

        Returns:
            是否成功
        """
        try:
            # 确保输出目录存在
            date_dir = Path(self.output_dir) / date
            self.file_manager.ensure_dir(date_dir)

            # 生成分类统计信息
            categories = {}
            for result in classification_results:
                categories[result.category] = categories.get(result.category, 0) + 1

            # 保存分类统计到JSON文件
            stats_file = date_dir / "classification_stats.json"
            stats_data = {
                "date": date,
                "total_papers": len(classification_results),
                "categories": categories,
                "classification_time": classification_results[0].classification_time if classification_results else ""
            }

            success = self.file_manager.save_json(stats_data, stats_file)
            if success:
                self.logger.info(f"保存分类统计: {stats_file}")
            else:
                self.logger.error(f"保存分类统计失败: {stats_file}")

            return success

        except Exception as e:
            self.logger.error(f"保存分类结果异常: {e}")
            return False
    
    def get_classification_statistics(self) -> Dict[str, Any]:
        """
        获取分类统计信息
        
        Returns:
            统计信息字典
        """
        results_dir = Path(self.output_dir)
        
        if not results_dir.exists():
            return {"total_dates": 0, "dates": []}
        
        date_dirs = [d for d in results_dir.iterdir() if d.is_dir()]
        
        return {
            "total_dates": len(date_dirs),
            "dates": [d.name for d in date_dirs],
            "results_dir": str(results_dir)
        }


class MDGenerator:
    """
    MD文件生成器

    负责生成各种格式的Markdown文件
    """

    def __init__(self):
        """初始化MD生成器"""
        self.logger = get_logger('md_generator')

    def generate_category_md(self, category: str,
                           classification_results: List[ClassificationResult],
                           date: str) -> str:
        """
        生成分类MD内容

        Args:
            category: 分类名称
            classification_results: 该分类的分类结果列表
            date: 日期字符串

        Returns:
            MD内容字符串
        """
        content = f"# {category}\n\n"
        content += f"生成日期：{date}\n"
        content += f"论文数量：{len(classification_results)} 篇\n\n"

        # 添加每篇论文的内容
        for i, result in enumerate(classification_results, 1):
            content += f"## 论文 {i}\n\n"
            content += result.md_content
            content += "\n\n---\n\n"

        return content

    def generate_summary_md(self, summary: AnalysisSummary) -> str:
        """
        生成汇总MD内容

        Args:
            summary: 分析汇总对象

        Returns:
            汇总MD内容
        """
        content = f"# 论文分类汇总报告\n\n"
        content += f"生成时间：{summary.date}\n\n"
        content += f"## 分类统计\n\n"
        content += f"- **总论文数**：{summary.total_papers} 篇\n"
        content += f"- **分类数量**：{len(summary.categories)} 个\n\n"
        content += f"## 各分类详情\n\n"

        # 按论文数量排序
        sorted_categories = sorted(summary.categories.items(),
                                 key=lambda x: x[1], reverse=True)

        for category, count in sorted_categories:
            percentage = count / max(summary.total_papers, 1) * 100
            content += f"### {category}\n"
            content += f"- 论文数量：{count} 篇\n"
            content += f"- 占比：{percentage:.1f}%\n\n"

        return content

    def generate_paper_md(self, analysis_result: AnalysisResult) -> str:
        """
        生成单篇论文的MD内容

        Args:
            analysis_result: 分析结果

        Returns:
            论文MD内容
        """
        content = f"# {analysis_result.translation}\n\n"
        content += f"**论文标题**：{analysis_result.title}\n"
        content += f"**中文标题**：{analysis_result.translation}\n"
        content += f"**论文地址**：{analysis_result.paper_url}\n\n"
        content += f"**作者团队**：{analysis_result.authors}\n"
        content += f"**发表日期**：{analysis_result.publish_date}\n"
        content += f"**模型功能**：{analysis_result.model_function}\n"

        return content


# 便捷函数
def create_classifier(config: Dict[str, Any]) -> PaperClassifier:
    """
    便捷函数：创建分类器实例

    Args:
        config: 配置字典

    Returns:
        PaperClassifier实例
    """
    return PaperClassifier(config)

def classify_papers(analysis_results: List[AnalysisResult], date: str = None,
                   output_dir: str = 'data/analysis_results',
                   ai_model: str = 'zhipu', silent: bool = False) -> List[ClassificationResult]:
    """
    便捷函数：分类论文

    Args:
        analysis_results: 分析结果列表
        date: 日期字符串
        output_dir: 输出目录
        ai_model: AI模型类型
        silent: 是否静默模式

    Returns:
        分类结果列表
    """
    config = {
        'output_dir': output_dir,
        'ai_model': ai_model
    }
    classifier = PaperClassifier(config)
    return classifier.classify_papers(analysis_results, date, silent)

def create_md_generator() -> MDGenerator:
    """
    便捷函数：创建MD生成器实例

    Returns:
        MDGenerator实例
    """
    return MDGenerator()
