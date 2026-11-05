package demo;

import java.io.IOException;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.*;

@RestController
public class BoardController {
    private final JdbcTemplate jdbc;

    public BoardController(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @PostMapping("/posts")
    public String create(@RequestParam String title, @RequestParam String body) {
        jdbc.update("INSERT INTO posts (title, body) VALUES (?, ?)", title, body);
        return "saved";
    }

    @GetMapping("/posts/{id}")
    public String show(@PathVariable String id) {
        String body = jdbc.queryForObject("SELECT body FROM posts WHERE id = ?", String.class, id);
        return "<article>" + body + "</article>";
    }

    @GetMapping("/hello")
    public void hello(HttpServletRequest req, HttpServletResponse res) throws IOException {
        String name = req.getParameter("name");
        res.getWriter().write("<p>Hello " + name + "</p>");
    }

    @GetMapping("/safe")
    public String safe(@RequestParam String q) {
        return "<p>" + org.springframework.web.util.HtmlUtils.htmlEscape(q) + "</p>";
    }
}
